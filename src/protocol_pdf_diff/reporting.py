"""Report writers for protocol PDF comparison results."""

from __future__ import annotations

import csv
import difflib
import html as html_lib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from .models import (
    DiffOptions,
    DiffResult,
    PageExtractionAudit,
    Section,
    SectionChange,
    SnippetPair,
    TableChange,
    TableRowChange,
    TableVisual,
)
from .quality import (
    DiffProvenance,
    DocumentQualityMetrics,
    PairAssessment,
    ReliabilityState,
    SUPPORTED_PROFILE,
)
from .table_codec import decode_table_cell, split_table_cells, split_table_field
from .text_utils import (
    CHINESE_COUNT_UNIT_PATTERN,
    CHINESE_NUMBER_CHARS,
    TABLE_NUMBER_DASH_CLASS,
    canonicalize_chinese_number_token,
    compact_inline,
    has_observable_identifier_boundary,
    identifier_boundary_signatures,
    is_known_engineering_symbol_letter_suffix,
    micro_identifier_signatures,
    normalize_table_number_dashes,
    parse_number_word_phrase,
    readable_symbol_font_glyphs,
    reader_safe_glyphs,
    reader_symbol_mapping_key,
    truncate,
)

_CHANGE_LABELS = {
    "added": "新增",
    "deleted": "删除",
    "modified": "修改",
    "review": "需复核",
    "unchanged": "未变化",
}
_MATCH_BASIS_LABELS = {
    "similarity_exact": "编号结构与正文相似度",
    "similarity_fallback": "正文相似度回退配对",
    "structural_prose_identity": "剔除结构化表格行后正文一致",
    "structural_unique_anchor": "同父同层唯一的标题技术锚点+第二正文证据",
    "structural_adjacent_brackets": "前后相邻章节共同确认",
    "structural_shift_run_body": "普通兄弟章节证明一致编号偏移+多条独立正文证据",
    "structural_shift_bracketed_sentence": "前后普通兄弟夹定一致编号偏移+标题相关正文句",
    "structural_mapped_parent_body": "已配对父章节改号+子章节正文相似度",
    "structural_mapped_parent_boundary": "已配对父章节改号+边界兄弟章节+直属子章节",
    "unmatched": "未配对",
}
_STRUCTURAL_MATCH_BASES = frozenset(
    {
        "structural_prose_identity",
        "structural_unique_anchor",
        "structural_adjacent_brackets",
        "structural_shift_run_body",
        "structural_shift_bracketed_sentence",
        "structural_mapped_parent_body",
        "structural_mapped_parent_boundary",
    }
)
_TABLE_PAIR_SIMILARITY_THRESHOLD = 0.65  # 表格模糊配对与跨章节唯一表题共用同一内容证据门槛。
_TABLE_PAGE_EDGE_MAX_FRACTION = 0.12  # 实测续表距页边约 7%/10%；留 2% 截图外扩余量仍拒绝中页表。
_READER_EMPTY_VALUE = "（空白）"  # 读者界面不暴露内部 <empty> 哨兵字符串。

_RELIABILITY_LABELS = {
    ReliabilityState.RELIABLE: "可靠",
    ReliabilityState.DEGRADED: "需人工复核",
    ReliabilityState.INDETERMINATE: "无法判断",
}
_READER_EXTRACTION_WARNING_REASON_RE = re.compile(
    r"^(?:旧协议|新协议)有\s+\d+\s+条抽取警告。$"
)
_READER_LAYOUT_SNIPPET_MIN_CHARS = 160
_READER_TABLE_ITEM_MAX_CHARS = 180
_READER_TABLE_VALUE_MAX_CHARS = 240
_READER_LAYOUT_HEADER_RE = re.compile(
    r"(?i)\b(?:parameter|symbol|value|units?|conditions?|index|transition|threshold|"
    r"level|label|description|reference|first|last|min(?:imum)?|max(?:imum)?|typ(?:ical)?)\b"
)
_READER_PROSE_VERB_RE = re.compile(
    r"(?i)\b(?:shall|should|must|may|can|is|are|was|were|be|being|been|verify|document|"
    r"obtain|provide|preserve|require|requires|required|specify|specifies|specified|"
    r"measure|measures|measured|use|uses|used|meet|meets|include|includes|included)\b"
    r"|应|必须|不得|可|要求|规定|需|验证|记录|确认"
)
_READER_NORMATIVE_VERB_RE = re.compile(
    r"(?i)\b(?:shall|should|must|may|can|verify|document|obtain|provide|preserve|"
    r"require|requires|required|specify|specifies|specified|measure|measures|measured|"
    r"meet|meets|include|includes|included|record|records|recorded)\b"
    r"|应|必须|不得|要求|规定|需|验证|记录|确认"
)
_READER_LEADING_NORMATIVE_RE = re.compile(
    r"(?i)\b(?:shall|must|should|required|requirements?|不得|必须|应当|要求|规定)\b"
)  # 图轴判定只否决从开头就是规范条款的片段，不把 Figure measured 误当正文门禁。
_READER_PROSE_TAIL_START_RE = re.compile(
    r"(?i)(?:[.!?。！？)]\s+)(?=(?:in\s+addition|however|the|this|these|those|it|"
    r"for|when|where|note|each|all|receiver|transmitter|a|an)\b)"
    r"|(?:[,;]\s+)(?=(?:where|whereas)\b)"
    r"|(?:\d\s+)(?=(?:in\s+addition|however|the|this|these|those|it|for|when|"
    r"where|note|each|all|receiver|transmitter)\b)"
    r"|(?:\s+)(?=(?:where\s+port\b|editor[’']s\s+note:))"
    r"|(?:[。！？]\s*)(?=(?:本|该|此|接收机|发射机|其中|此外|对于))"
)
_READER_DIAGRAM_LABEL_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:TP\d+[a-z]?|HCB|MCB|CTLE|DFE|CRU|VNA|scope|"
    r"generator|calibration|terminations?|interfaces?|crosstalk|stressed|signal|"
    r"sinusoidal|insertion|arrow|reference|block)(?![A-Za-z0-9])"
)
_READER_DIAGRAM_LABEL_SINGLE_RE = re.compile(
    r"(?i)^(?:TP\d+[a-z]?|HCB|MCB|CTLE|DFE|CRU|VNA|scope|reference|terminations?)$"
)

_INLINE_TOKEN_RE = re.compile(
    r"<=>|<->|->|<-|=>|→|←|↔|⇒|⇐|⇔|⟶|⟵"
    r"|<=|>=|!=|==|≤|≥|≠"
    r"|</?(?:[^\W\d_]|_)[\w:.-]*\s*/?>"
    r"|[$@#](?:[^\W\d_]|_)\w*"
    r"|/(?:[\w.*+?^$()\[\]{}|-]+)/"
    r"|(?:[a-z]:\\|\\\\|(?:[^\W\d_]|_)[\w.-]*\\)(?:[\w.-]+\\)*[\w.-]+"
    r"|--?(?:[^\W\d_]|_)[\w-]*"
    r"|(?<!\w)/(?:[\w.-]+/)*[\w.-]+"
    r"|(?<![\w.])0x[0-9a-f]+(?!\w)"
    r"|(?<!\w)(?:\d+(?:\.\d+){2,}|(?=[\w.-]*\d)(?:[^\W\d_]|_)[\w-]*(?:\.[\w-]+)+)(?!\w)"
    r"|[+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*(?:x|×|\*)\s*10\s*"
    r"\^\s*[+-]?\d+"
    r"|[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?"
    r"|(?<=\d)[x×*](?=(?:\d|[^\W\d_]|_))"
    r"|[\u3400-\u4dbf\u4e00-\u9fff]+"
    r"|(?:[^\W\d_\u3400-\u4dbf\u4e00-\u9fff]|_)[^\W\u3400-\u4dbf\u4e00-\u9fff]*"
    r"(?:[-/][^\W\u3400-\u4dbf\u4e00-\u9fff]+)*"
    r"|['\"‘’“”]"
    r"|\.{2,}|,{2,}|;{2,}|:{2,}|\?{2,}|[.,;:?]"
    r"|[^\w\s.,;:?\"“”'‘’]",
    flags=re.I,
)
_XML_TAG_RE = re.compile(r"</?(?:[^\W\d_]|_)[\w:.-]*\s*/?>")
_VARIABLE_LITERAL_RE = re.compile(r"[$@#](?:[^\W\d_]|_)\w*")
_SLASH_LITERAL_RE = re.compile(r"/(?:[\w.*+?^$()\[\]{}|-]+)/")
_BACKSLASH_PATH_RE = re.compile(
    r"(?:[a-z]:\\|\\\\|(?:[^\W\d_]|_)[\w.-]*\\)(?:[\w.-]+\\)*[\w.-]+",
    flags=re.I,
)
_CLI_OPTION_RE = re.compile(r"--?(?:[^\W\d_]|_)[\w-]*")
_ABSOLUTE_PATH_RE = re.compile(r"/(?:[\w.-]+/)*[\w.-]+")
_HEX_LITERAL_RE = re.compile(r"(?i)(?<![\w.])0x[0-9a-f]+(?!\w)")
_DOTTED_IDENTIFIER_RE = re.compile(
    r"(?:\d+(?:\.\d+){2,}|(?=[\w.-]*\d)(?:[^\W\d_]|_)[\w-]*(?:\.[\w-]+)+)"
)
_INLINE_NUMBER_RE = re.compile(
    r"[+-]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?",
    flags=re.I,
)
_TABLE_CASE_BEARING_TOKEN_RE = re.compile(
    r"(?<![^\W\d_\u3400-\u4dbf\u4e00-\u9fff])"
    r"(?:[^\W\d_\u3400-\u4dbf\u4e00-\u9fff]|_)"
    r"[^\W\u3400-\u4dbf\u4e00-\u9fff]*(?:[-/][^\W\u3400-\u4dbf\u4e00-\u9fff]+)*"
    r"(?![^\W\d_\u3400-\u4dbf\u4e00-\u9fff])"
)  # 标识符形态的大小写可能有语义；普通句首首字母仍作为格式噪声处理。
_DIRECTIONAL_SYMBOL_KEYS = {
    "->": "arrow:right",
    "→": "arrow:right",
    "⟶": "arrow:right",
    "<-": "arrow:left",
    "←": "arrow:left",
    "⟵": "arrow:left",
    "<->": "arrow:bidirectional",
    "↔": "arrow:bidirectional",
    "=>": "arrow:implies-right",
    "⇒": "arrow:implies-right",
    "⇐": "arrow:implies-left",
    "<=>": "arrow:equivalence",
    "⇔": "arrow:equivalence",
}
_UNICODE_BULLET_MARKERS = frozenset({"•", "●", "⚫", ""})
_SEMANTIC_OPERATOR_KEYS = {
    "+": "plus",
    "-": "minus",
    "−": "minus",
    "*": "multiply",
    "×": "multiply",
    "/": "divide",
    "÷": "divide",
    "\u2061": "function-application",
    "\u2062": "multiply",
    "\u2063": "separator",
    "\u2064": "plus",
}
_CHINESE_INLINE_NUMBER_RE = re.compile(
    rf"[{CHINESE_NUMBER_CHARS}]+(?=\s*(?:{CHINESE_COUNT_UNIT_PATTERN}))",
    flags=re.I,
)
_IDENTIFIER_NUMBER_LABELS = frozenset(
    {
        "build",
        "code",
        "codes",
        "id",
        "identifier",
        "model",
        "models",
        "part",
        "profile",
        "profiles",
        "rev",
        "revision",
        "serial",
        "symbol",
        "version",
        "versions",
    }
)
_TABLE_METADATA_TITLE_RE = re.compile(
    r"(?i)\b(?:revision|change|version)\s+(?:history|record|log)\b|修订(?:历史|记录)|变更(?:历史|记录)"
)  # 出版修订记录属于文档元信息，但仍保留完整表格证据。


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
    replacements. Markdown/TXT remain useful for copy-paste workflows, separate
    prose/table CSV files support Excel review, and JSON preserves the complete
    section and table-change audit model.
    """

    base_dir = Path(output_dir).expanduser().resolve()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = base_dir / f"protocol_diff_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    table_groups = _paired_table_visuals(
        result.old_table_visuals,
        result.new_table_visuals,
        old_sections=result.old_sections,
        new_sections=result.new_sections,
    )  # 读者正文去重需要全部已配对表，包括因内容完全相同而不生成变化卡的表。
    table_changes = _ordered_table_changes(_build_table_changes(result))  # 表格事实只计算一次，并在所有格式中保持技术表优先。
    reader_table_changes = _reader_table_changes(table_changes)
    reader_table_evidence: list[TableChange | _TableVisualGroup] = [
        *table_changes,
        *table_groups,
    ]  # 变化表携带显式复核卡；未变化且可靠的表仍由完整配对组提供去重证据。
    reader_changes: list[SectionChange] = []
    for change in result.changes:
        # 作者、邮箱、版权和修订记录只保留在 JSON/CSV 审计面，不再进入三种读者报告。
        if change.role == "document_metadata":
            continue
        reader_change = _reader_section_change(change, reader_table_evidence)
        if reader_change is not None:
            reader_changes.append(reader_change)
    reader_result = replace(
        result,
        changes=reader_changes,
    )  # 读者层可把纯版面顺序不确定性降为复核或去除坐标已证明的表格重复；JSON/CSV 继续保存原始比较事实。
    markdown = _render_markdown(reader_result, options, reader_table_changes)
    html = _render_html(reader_result, options, reader_table_changes)
    text = _markdown_to_plain_text(markdown)
    csv_rows = _rows_for_csv(result.changes)
    table_csv_rows = _rows_for_table_csv(table_changes)
    sections_payload = {
        "old_pdf": str(result.old_pdf),
        "new_pdf": str(result.new_pdf),
        "old_total_pages": _source_page_count(result, "old"),
        "new_total_pages": _source_page_count(result, "new"),
        "old_selected_pages": _selected_page_payload(result, "old"),
        "new_selected_pages": _selected_page_payload(result, "new"),
        "changes": [_change_to_dict(change) for change in result.changes],
        "table_changes": [_table_change_to_dict(change) for change in table_changes],
        "old_sections": [_section_to_dict(section) for section in result.old_sections],
        "new_sections": [_section_to_dict(section) for section in result.new_sections],
        "old_table_visuals": [_table_visual_to_dict(table) for table in result.old_table_visuals],
        "new_table_visuals": [_table_visual_to_dict(table) for table in result.new_table_visuals],
        "warnings": result.warnings,
        "assessment": _assessment_to_dict(_assessment_for_report(result)),
        "provenance": _provenance_to_dict(result.provenance),
        "extraction_audit": {
            "old": _extraction_audit_to_dict(result.old_extraction_audit),
            "new": _extraction_audit_to_dict(result.new_extraction_audit),
        },  # 逐页解析事实单独输出，绝不把 PageText 或 DocumentBlock 原文放入审计记录。
    }

    md_path = report_dir / "protocol_diff_report.md"
    html_path = report_dir / "protocol_diff_report.html"
    txt_path = report_dir / "protocol_diff_report.txt"
    csv_path = report_dir / "changes.csv"
    table_csv_path = report_dir / "table_changes.csv"
    json_path = report_dir / "protocol_diff_data.json"

    md_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    txt_path.write_text(text, encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "change_type",
                "role",
                "report_location",
                "new_location",
                "old_location",
                "new_pages",
                "old_pages",
                "similarity",
                "match_basis",
                "match_basis_label",
                "summary",
                "added_snippets",
                "removed_snippets",
                "replaced_snippets",
                "omitted_snippet_count",
            ],
        )
        writer.writeheader()
        writer.writerows(csv_rows)
    with table_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "table_change_type",
                "role",
                "old_titles",
                "new_titles",
                "old_pages",
                "new_pages",
                "pair_similarity",
                "item",
                "old_value",
                "new_value",
                "row_change_type",
            ],
        )
        writer.writeheader()
        writer.writerows(table_csv_rows)
    json_path.write_text(json.dumps(sections_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "report_dir": report_dir,
        "markdown": md_path,
        "html": html_path,
        "text": txt_path,
        "csv": csv_path,
        "table_csv": table_csv_path,
        "json": json_path,
    }


def _render_markdown(
    result: DiffResult,
    options: DiffOptions,
    table_changes: list[TableChange],
) -> str:
    """Render the main review report in Markdown."""

    counts = _change_counts(result.changes)
    # write_reports 已把元信息从读者副本剔除；此处只渲染技术正文，避免空板块和零值指标占空间。
    technical_changes = [change for change in result.changes if change.role == "technical"]
    technical_review_count = sum(
        change.change_type == "review" for change in technical_changes
    )
    material_technical_count = len(technical_changes) - technical_review_count
    table_changes = _ordered_table_changes(table_changes)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    comparison_note = _comparison_method_note(result)
    scope_note = _report_scope_note(options)
    assessment = _assessment_for_report(result)
    material_table_changes = _material_table_changes(table_changes)
    table_row_change_count = sum(
        len(_material_table_row_changes(change))
        for change in table_changes
    )
    table_review_count = sum(
        len(_table_review_rows(change))
        for change in table_changes
    )
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
        f"- 比较方式: {comparison_note}",
        f"- 报告范围: {scope_note}",
        "",
        "## 识别可信度",
        "",
        f"- 状态: {_RELIABILITY_LABELS[assessment.state]} (`{assessment.state.value}`)",
        f"- 结论: {assessment.headline}",
        f"- 当前支持范围: {assessment.supported_profile}",
        *[f"- 原因: {reason}" for reason in _reader_assessment_reasons(assessment)],
        "",
        "## 汇总",
        "",
        "| 类型 | 数量 |",
        "|---|---:|",
        f"| 核心技术变化 | {material_technical_count} |",
        f"| 正文字符复核项 | {technical_review_count} |",
        f"| 章节修改 / 新增 / 删除 | {counts.get('modified', 0)} / {counts.get('added', 0)} / {counts.get('deleted', 0)} |",
        f"| 变化表格 | {len(material_table_changes)} |",
        f"| 表格行变化 | {table_row_change_count} |",
        f"| 表格复核项 | {table_review_count} |",
        "",
    ]

    lines.extend(
        [
            "## 技术正文变化与复核",
            "",
            "说明: 页码来自 PDF 抽取顺序；如果 PDF 自身页脚页码不同，请以 PDF 阅读器显示为准。",
            "",
        ]
    )
    _append_markdown_changes(lines, technical_changes, start_index=1)
    if not technical_changes:
        message = (
            _empty_report_message(result)
            if not table_changes
            else "未列出技术正文变化；是否可确认一致请以顶部识别可信度为准。"
        )
        lines.extend([message, ""])

    if table_changes:
        lines.extend(["## 表格补充证据（变化与复核）", ""])
        for index, table_change in enumerate(table_changes, start=1):
            lines.append(f"### T{index}. {_table_change_title(table_change)}")
            lines.append(f"- 角色: {table_change.role}")
            lines.append(f"- 类型: {_CHANGE_LABELS.get(table_change.change_type, table_change.change_type)}")
            lines.append(f"- 旧表: {_table_side_description(table_change.old_tables)}")
            lines.append(f"- 新表: {_table_side_description(table_change.new_tables)}")
            if table_change.old_tables and table_change.new_tables:
                lines.append(f"- 配对相似度: {table_change.similarity:.3f}")
            if table_change.caption_changed:
                lines.append("- 表题/表号发生变化；行内容变化另列如下。")
            for row_change in table_change.row_changes:
                reader_old_value = _reader_table_inline_text(row_change.old_value)
                reader_new_value = _reader_table_inline_text(row_change.new_value)
                difference_hint = _reader_pair_difference_hint(
                    reader_old_value,
                    reader_new_value,
                )
                item = _reader_table_cell_text(
                    _reader_table_inline_text(row_change.item),
                    max_chars=_READER_TABLE_ITEM_MAX_CHARS,
                )
                old_value = _reader_table_cell_text(
                    reader_old_value,
                    max_chars=_READER_TABLE_VALUE_MAX_CHARS,
                    difference_hint=difference_hint,
                )
                new_value = _reader_table_cell_text(
                    reader_new_value,
                    max_chars=_READER_TABLE_VALUE_MAX_CHARS,
                    difference_hint=difference_hint,
                )
                lines.append(
                    f"- {row_change.change_type}: {item} | "
                    f"旧 `{old_value}` | 新 `{new_value}`"
                )
                if glyph_note := _unverified_pua_mapping_note(
                    row_change.old_value,
                    row_change.new_value,
                    reader_old_text=reader_old_value,
                    reader_new_text=reader_new_value,
                    force_reader_equivalent=(
                        row_change.change_type == "需人工复核"
                        and _table_row_display_key(reader_old_value)
                        == _table_row_display_key(reader_new_value)
                    ),
                ):
                    lines.append(f"  - 说明: {glyph_note}")
            lines.append("")

    return reader_safe_glyphs("\n".join(lines))
    # Markdown 及由它派生的 TXT 都是读者界面；JSON/CSV 仍保留原始 PUA 审计值。


def _append_markdown_changes(
    lines: list[str],
    changes: list[SectionChange],
    *,
    start_index: int,
) -> int:
    """Append one role group and return the next global card index."""

    for index, change in enumerate(changes, start=start_index):
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
            if change.match_basis in _STRUCTURAL_MATCH_BASES:
                lines.append(
                    "- 配对依据: "
                    f"{_MATCH_BASIS_LABELS[change.match_basis]}"
                    "（结构证据授权；上方相似度仍为全文实际值）"
                )
        summary = _change_summary(change)
        if summary:
            lines.append(f"- 差异摘要: {summary}")

        if change.replaced_snippets:
            lines.append("- 替换片段:")
            for pair in change.replaced_snippets:
                difference_hint = _reader_pair_difference_hint(pair.old, pair.new)
                lines.append(
                    f"  - 旧: {_reader_snippet_text(pair.old, difference_hint=difference_hint)}"
                )
                lines.append(
                    f"    新: {_reader_snippet_text(pair.new, difference_hint=difference_hint)}"
                )
                if glyph_note := _unverified_pua_mapping_note(pair.old, pair.new):
                    lines.append(f"    说明: {glyph_note}")
        if change.added_snippets:
            lines.append("- 新增片段:")
            for snippet in _reader_single_list_groups(change.added_snippets):
                lines.append(
                    "  - "
                    + _reader_snippet_text(
                        snippet,
                        difference_hint=_reader_single_side_evidence_hint(snippet),
                    )
                )
        if change.removed_snippets:
            lines.append("- 删除片段:")
            for snippet in _reader_single_list_groups(change.removed_snippets):
                lines.append(
                    "  - "
                    + _reader_snippet_text(
                        snippet,
                        difference_hint=_reader_single_side_evidence_hint(snippet),
                    )
                )
        if change.omitted_snippet_count:
            lines.append(f"- {_omitted_snippet_message(change.omitted_snippet_count)}")
        lines.append("")
    return start_index + len(changes)


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


def _render_html(
    result: DiffResult,
    options: DiffOptions,
    table_changes: list[TableChange],
) -> str:
    """Render an easy-to-scan standalone HTML review report."""

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    comparison_note = _comparison_method_note(result)
    scope_note = _report_scope_note(options)
    assessment_html = _render_assessment_html(_assessment_for_report(result))
    # HTML 与 Markdown 共用技术正文口径，元信息只留在机器审计文件。
    technical_changes = [change for change in result.changes if change.role == "technical"]
    technical_review_count = sum(
        change.change_type == "review" for change in technical_changes
    )
    material_technical_count = len(technical_changes) - technical_review_count
    table_changes = _ordered_table_changes(table_changes)
    indexed_technical = list(enumerate(technical_changes, start=1))
    title = "协议 PDF 差异报告"
    technical_nav_items = "\n".join(
        _render_nav_item(index, change) for index, change in indexed_technical
    )
    table_nav_items = "\n".join(
        _render_table_nav_item(index, change)
        for index, change in enumerate(table_changes, start=1)
    )
    nav_parts: list[str] = []
    if technical_nav_items:
        nav_parts.extend(['<div class="nav-title">技术正文变化与复核</div>', technical_nav_items])
    if table_nav_items:
        nav_parts.extend(['<div class="nav-title nav-section-gap">表格补充证据</div>', table_nav_items])
    nav_items = "\n".join(nav_parts)
    if not nav_items:
        nav_items = f'<div class="empty-nav">{_escape(_empty_report_message(result))}</div>'

    technical_cards = "\n".join(
        _render_change_html(index, change) for index, change in indexed_technical
    )
    if not technical_cards:
        technical_message = (
            _empty_report_message(result)
            if not table_changes
            else "未列出技术正文变化；是否可确认一致请以顶部识别可信度为准。"
        )
        technical_cards = f'<section class="empty-state">{_escape(technical_message)}</section>'
    table_visual_html = _render_table_changes_html(table_changes)
    material_table_changes = _material_table_changes(table_changes)
    table_row_change_count = sum(
        len(_material_table_row_changes(change))
        for change in table_changes
    )
    table_review_count = sum(
        len(_table_review_rows(change))
        for change in table_changes
    )

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
    .meta, .change-card, .empty-state {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-bottom: 16px;
      padding: 16px;
    }}
    .assessment-banner {{
      margin: 18px 22px 0;
      padding: 16px 18px;
      border: 2px solid var(--line);
      border-left-width: 8px;
      border-radius: 8px;
      background: var(--panel);
    }}
    .assessment-banner h2 {{ font-size: 20px; }}
    .assessment-banner p {{ margin: 6px 0 0; }}
    .assessment-banner ul {{ margin: 8px 0 0; padding-left: 20px; }}
    .assessment-reliable {{ border-left-color: var(--add); }}
    .assessment-degraded {{ border-left-color: var(--mod); }}
    .assessment-indeterminate {{ border-left-color: var(--del); }}
    .meta dl {{ display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 6px 12px; margin: 0; }}
    .meta dt {{ color: var(--muted); }}
    .meta dd {{ margin: 0; overflow-wrap: anywhere; }}
    .nav-title {{ color: var(--muted); font-size: 13px; margin-bottom: 10px; }}
    .nav-section-gap {{ margin-top: 18px; }}
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
    .badge-review {{ color: var(--mod); background: var(--mod-bg); border-color: #f1d489; }}
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
    .snippet-detail {{
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #f7f9fc;
      overflow: hidden;
    }}
    .snippet-detail summary {{
      cursor: pointer;
      color: var(--muted);
      padding: 9px 11px;
      font-weight: 600;
    }}
    .snippet-detail[open] summary {{ border-bottom: 1px solid var(--line); }}
    .snippet-detail-body {{ padding: 10px; overflow-wrap: anywhere; }}
    .snippet-detail-layout summary {{ color: var(--mod); }}
    .snippet-visible-tail {{
      margin-top: 8px;
      padding: 8px 10px;
      border-left: 3px solid var(--blue);
      background: #f4f8fd;
      color: var(--ink);
    }}
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
    .match-basis {{
      color: var(--blue);
      background: #eef5fd;
      border: 1px solid #c9dcf3;
      border-radius: 8px;
      padding: 8px 10px;
      margin: 8px 0 12px;
      font-size: 13px;
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
    .table-cell-detail summary {{ cursor: pointer; color: var(--mod); }}
    .table-cell-detail-body {{ margin-top: 6px; overflow-wrap: anywhere; white-space: normal; }}
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
    .table-kind-review {{ color: #7a4b00; background: #fff7db; border-color: #f5d889; }}
    .section-heading {{ margin: 22px 0 12px; font-size: 21px; }}
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
  {assessment_html}
  <div class="layout">
    <aside>
      {nav_items}
    </aside>
    <main>
      <section class="summary">
        <div class="metric"><strong>{material_technical_count}</strong><span>核心技术变化</span></div>
        <div class="metric"><strong>{technical_review_count}</strong><span>正文字符复核项</span></div>
        <div class="metric"><strong>{len(material_table_changes)}</strong><span>变化表格</span></div>
        <div class="metric"><strong>{table_row_change_count}</strong><span>表格行变化</span></div>
        <div class="metric"><strong>{table_review_count}</strong><span>表格复核项</span></div>
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
      <h2 class="section-heading" id="text-changes">技术正文变化</h2>
      {technical_cards}
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
    match_basis_html = ""
    if change.match_basis in _STRUCTURAL_MATCH_BASES:
        match_basis_html = (
            '<div class="match-basis">配对依据：'
            f'{_escape(_MATCH_BASIS_LABELS[change.match_basis])}'
            '（结构证据授权；相似度仍为全文实际值）</div>'
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
        {match_basis_html}
        {summary_html}
        {body}
      </section>
    """


def _render_table_changes_html(table_changes: list[TableChange]) -> str:
    """Render only changed screenshot-backed tables from the shared fact model."""

    if not table_changes:
        return ""
    cards = "\n".join(
        _render_table_change_html(index, change)
        for index, change in enumerate(table_changes, start=1)
    )
    return f"""
      <section class="table-visuals" id="table-changes">
        <h2>表格补充证据（变化与复核）</h2>
        <p class="change-summary">展示检测到的行级变化、表题/表号变化、单侧新增/删除，以及结构或字体编码尚未验证的表格复核项；旧/新表题、页码、配对分数和完整事实同时写入 JSON 与 CSV。</p>
        {cards}
      </section>
    """


def _build_table_changes(result: DiffResult) -> list[TableChange]:
    """Pair visual tables and materialize one reusable list of changed facts."""

    changes: list[TableChange] = []
    for group in _paired_table_visuals(
        result.old_table_visuals,
        result.new_table_visuals,
        old_sections=result.old_sections,
        new_sections=result.new_sections,
    ):
        if _table_group_is_isolated_one_cell_image_fragment(group):
            continue  # 图中孤立短标签即使被网格检测框住，也没有足够证据宣称新增/删除表格。
        row_change_list = _table_row_changes(group.old_tables, group.new_tables)
        unreliable_multirow_alignment = _table_group_has_unreliable_multirow_alignment(group)
        alignment_review_is_reader_evidence = bool(
            unreliable_multirow_alignment
            and group.old_tables
            and group.new_tables
            and all(
                table.content_fully_represented
                for table in (*group.old_tables, *group.new_tables)
            )
        )
        if row_change_list and unreliable_multirow_alignment:
            row_change_list = [
                replace(row_change, change_type="需人工复核")
                for row_change in row_change_list
            ]
        if alignment_review_is_reader_evidence:
            row_change_list.append(
                TableRowChange(
                    item="表格行归属",
                    old_value="多行单元格归属未验证",
                    new_value="多行单元格归属未验证",
                    change_type="需人工复核",
                )
            )  # 即使各字符未变化，也必须先给读者一条紧凑复核卡，才能安全折叠重复正文墙。
        if review_change := _text_backed_table_structure_review_change(group):
            row_change_list.append(review_change)
        caption_review = _table_caption_pua_review_change(group)
        if caption_review is not None:
            row_change_list.append(caption_review)
        row_changes = tuple(row_change_list)
        caption_changed = (
            _table_group_caption_changed(group.old_tables, group.new_tables)
            and caption_review is None
        )
        if group.old_tables and group.new_tables and not row_changes and not caption_changed:
            continue  # 未变化表格不占导航和报告篇幅。
        change_type = "modified"
        if not group.old_tables:
            change_type = "added"
        elif not group.new_tables:
            change_type = "deleted"
        elif (
            row_changes
            and not caption_changed
            and not any(
                row.change_type != "需人工复核"
                for row in row_changes
            )
        ):
            change_type = "review"  # 内容相同但行列边界未知，不得冒充已确认“修改”。
        similarity = (
            _table_visual_group_similarity(group.old_tables, group.new_tables)
            if group.old_tables and group.new_tables
            else 0.0
        )  # 报告分数与配对门槛共用整组表页，避免续页证据通过门槛却显示首屏低分。
        changes.append(
            TableChange(
                change_type=change_type,
                old_tables=group.old_tables,
                new_tables=group.new_tables,
                similarity=similarity,
                caption_changed=caption_changed,
                row_changes=row_changes,
                role=_table_change_role(group, row_changes),
            )
        )
    return changes


def _table_group_has_unreliable_multirow_alignment(group: _TableVisualGroup) -> bool:
    """Return whether a paired multirow table lacks row-assignment proof."""

    if not group.old_tables or not group.new_tables:
        return False

    def side_has_unreliable_multirow_assignment(tables: tuple[TableVisual, ...]) -> bool:
        has_multiple_logical_rows = bool(
            sum(len(table.row_texts) for table in tables) > 1
            or any(
                "\\n" in row or "\n" in row
                for table in tables
                for row in table.row_texts
            )
        )
        return has_multiple_logical_rows and any(
            not table.row_alignment_reliable for table in tables
        )

    # A one-row exact reconstruction has no cross-row assignment to guess.  It
    # must not downgrade a physically aligned multirow source table's proven
    # appended/deleted row, while an aggregate or multirow side still fails closed.
    return side_has_unreliable_multirow_assignment(group.old_tables) or (
        side_has_unreliable_multirow_assignment(group.new_tables)
    )


def _text_backed_table_structure_review_change(
    group: _TableVisualGroup,
) -> TableRowChange | None:
    """Keep flat-text table reconstruction visible without claiming structural equality."""

    old_description = _table_structure_side_description(group.old_tables)
    new_description = _table_structure_side_description(group.new_tables)
    if not any(
        table.ocr_status == "text_backed_exact_match"
        for table in (*group.old_tables, *group.new_tables)
    ):
        return None
    return TableRowChange(
        item="表格结构复核",
        old_value=old_description,
        new_value=new_description,
        change_type="需人工复核",
    )  # 内容逐字匹配只用于折叠重复原文；该行确保报告从不把未知物理结构静默判成相同。


def _table_caption_pua_review_change(
    group: _TableVisualGroup,
) -> TableRowChange | None:
    """Keep an unproven PUA/Unicode caption mapping as review evidence."""

    if not group.old_tables or not group.new_tables:
        return None
    old_titles = " / ".join(_unique_table_titles(group.old_tables))
    new_titles = " / ".join(_unique_table_titles(group.new_tables))
    if not _unverified_pua_mapping_note(old_titles, new_titles):
        return None
    return TableRowChange(
        item="表题字体编码复核",
        old_value=old_titles,
        new_value=new_titles,
        change_type="需人工复核",
    )


def _table_structure_side_description(
    tables: tuple[TableVisual, ...],
) -> str:
    """Describe absent, structured, flat, and mixed evidence without overclaiming."""

    if not tables:
        return "无对应表格"
    text_backed_count = sum(
        table.ocr_status == "text_backed_exact_match"
        for table in tables
    )
    if text_backed_count == len(tables):
        return "扁平文字匹配；行列边界未验证"
    if text_backed_count:
        return "含扁平文字匹配；部分行列边界未验证"
    return "结构化表格"


def _table_review_rows(change: TableChange) -> tuple[TableRowChange, ...]:
    """Return uncertainty findings that must not inflate confirmed difference totals."""

    return tuple(
        row
        for row in change.row_changes
        if row.change_type == "需人工复核"
    )


def _material_table_row_changes(change: TableChange) -> tuple[TableRowChange, ...]:
    """Return confirmed table deltas separately from uncertainty findings."""

    return tuple(
        row
        for row in change.row_changes
        if row.change_type != "需人工复核"
    )


def _material_table_changes(changes: list[TableChange]) -> list[TableChange]:
    """Exclude review-only pairs from the reader's confirmed table-change count."""

    return [change for change in changes if change.change_type != "review"]


def _table_group_is_isolated_one_cell_image_fragment(
    group: _TableVisualGroup,
) -> bool:
    """Reject only an uncaptioned one-cell continuation cut from an image."""

    tables = group.old_tables or group.new_tables
    if group.old_tables and group.new_tables or len(tables) != 1:
        return False
    table = tables[0]
    if (
        table.title.strip()
        or not table.is_continuation
        or not table.image_data_uri
        or not table.ocr_status
        or len(table.row_texts) != 1
    ):
        return False
    entries = _table_row_field_entries(table.row_texts[0])
    return bool(
        len(entries) == 1
        and re.fullmatch(r"column\s+1", entries[0][2], flags=re.I)
        and 0 < len(compact_inline(entries[0][3])) <= 12
    )  # 有表题、多个单元格、多行或较长内容时均保守保留为表格事实。


def _table_change_role(
    group: _TableVisualGroup,
    row_changes: tuple[TableRowChange, ...],
) -> str:
    """Classify publication-history tables without hiding their facts."""

    titles = " ".join(
        table.title for table in (*group.old_tables, *group.new_tables) if table.title
    )
    if _TABLE_METADATA_TITLE_RE.search(titles) or any(
        _revision_record_table_key(table)
        for table in (*group.old_tables, *group.new_tables)
    ):
        return "document_metadata"
    revision_date_rows = sum(
        _row_change_has_revision_and_date(row_change) for row_change in row_changes
    )
    if revision_date_rows >= 2:
        return "document_metadata"
    return "technical"


def _row_change_has_revision_and_date(row_change: TableRowChange) -> bool:
    """Return True when one row exposes both revision and date fields."""

    text = " | ".join((row_change.item, row_change.old_value, row_change.new_value))
    return bool(
        re.search(r"(?i)\brevision\s*=", text)
        and re.search(r"(?i)\bdate\s*=", text)
    )


def _table_group_caption_changed(
    old_tables: tuple[TableVisual, ...],
    new_tables: tuple[TableVisual, ...],
) -> bool:
    """Return True when paired tables have different visible captions."""

    if not old_tables or not new_tables:
        return False  # 单侧表格的新增/删除已由 change_type 表达，不重复标记表题变化。
    old_titles = [
        _case_aware_display_text_key(
            _normalized_numbered_table_caption(title) or compact_inline(title)
        )
        for title in _unique_table_titles(old_tables)
    ]
    new_titles = [
        _case_aware_display_text_key(
            _normalized_numbered_table_caption(title) or compact_inline(title)
        )
        for title in _unique_table_titles(new_tables)
    ]
    return old_titles != new_titles


def _paired_table_visuals(
    old_tables: list[TableVisual],
    new_tables: list[TableVisual],
    *,
    old_sections: list[Section] | None = None,
    new_sections: list[Section] | None = None,
) -> list[_TableVisualGroup]:
    """Pair table visuals by logical table group without forcing weak matches."""

    old_unused = set(range(len(old_tables)))  # 未匹配旧表索引。
    new_unused = set(range(len(new_tables)))  # 未匹配新表索引。
    groups: list[_TableVisualGroup] = []  # 输出旧/新逻辑表格组。
    _pair_same_caption_table_groups(
        old_tables,
        new_tables,
        old_unused,
        new_unused,
        groups,
        old_sections=old_sections,
        new_sections=new_sections,
    )
    unique_exact_captions = _unique_exact_table_captions(old_tables, new_tables)
    supported_renumberings = _supported_table_renumberings(old_tables, new_tables)
    scored: list[tuple[float, int, int]] = []  # 保存所有足够可信的候选配对。
    for old_index, old_table in enumerate(old_tables):
        if old_index not in old_unused:
            continue
        old_context = _table_visual_section_context(old_table, old_sections or [])
        for new_index, new_table in enumerate(new_tables):
            if new_index not in new_unused:
                continue
            new_context = _table_visual_section_context(new_table, new_sections or [])
            supported_descriptive_renumbering = (
                _table_descriptive_renumbering_key(old_table, new_table)
                in supported_renumberings
            )
            old_caption = _table_visual_caption_key(old_table)
            new_caption = _table_visual_caption_key(new_table)
            exact_caption_identity = (
                bool(old_caption)
                and old_caption == new_caption
                and old_caption in unique_exact_captions
            )
            if (
                old_caption != new_caption
                and (
                    old_caption in unique_exact_captions
                    or new_caption in unique_exact_captions
                )
            ):
                continue  # 任一侧已有唯一同题候选时，局部换序不能把它按 ordinal 串配给另一编号表。
            exact_caption_content_supported = (
                exact_caption_identity
                and _table_visual_group_similarity(
                    (old_table,),
                    (new_table,),
                ) >= _TABLE_PAIR_SIMILARITY_THRESHOLD
            )  # 预配对拒绝后，单页后备通道也必须证明 caption 之外的行内容相关。
            if (
                old_context
                and new_context
                and old_context != new_context
                and not exact_caption_content_supported
                and not supported_descriptive_renumbering
            ):
                continue  # 无编号/模糊表也不得穿越 Part/章节配对，否则章节重排会抵消真实变化。
            if _same_page_table_ordinals_differ(
                old_tables,
                old_index,
                new_tables,
                new_index,
            ):
                continue  # 同页多章节没有 y 坐标归属证据：无编号表只能按页内 ordinal 配对。
            score = _table_visual_similarity(old_table, new_table)
            if score >= _TABLE_PAIR_SIMILARITY_THRESHOLD:
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


def _same_page_table_ordinals_differ(
    old_tables: list[TableVisual],
    old_index: int,
    new_tables: list[TableVisual],
    new_index: int,
) -> bool:
    """Keep every same-page table attached to its physical ordinal position."""

    old_table = old_tables[old_index]
    new_table = new_tables[new_index]
    if old_table.page_number != new_table.page_number:
        return False
    old_same_page = _same_page_table_indexes_in_physical_order(
        old_tables,
        old_table.page_number,
    )
    new_same_page = _same_page_table_indexes_in_physical_order(
        new_tables,
        new_table.page_number,
    )
    if len(old_same_page) <= 1 and len(new_same_page) <= 1:
        return False
    caption_order_preserved = _same_page_unique_caption_order_is_preserved(
        old_tables,
        old_same_page,
        old_index,
        new_tables,
        new_same_page,
        new_index,
    )
    if caption_order_preserved is not None:
        return not caption_order_preserved  # 共享唯一表题必须服从 LCS；绝对 ordinal 巧合相同也不能吞掉交叉换序。
    if old_same_page.index(old_index) == new_same_page.index(new_index):
        return False
    return True


def _same_page_table_indexes_in_physical_order(
    tables: list[TableVisual],
    page_number: int,
) -> list[int]:
    """Order one page's tables by bbox, falling back when coordinates are unusable."""

    indexes = [
        index
        for index, table in enumerate(tables)
        if table.page_number == page_number
    ]
    valid_positions = [
        position
        for position, index in enumerate(indexes)
        if _table_visual_has_valid_bbox(tables[index])
    ]
    if not valid_positions:
        return indexes
    sorted_valid_indexes = sorted(
        (indexes[position] for position in valid_positions),
        key=lambda index: (
            float(tables[index].bbox[1]),
            float(tables[index].bbox[0]),
            float(tables[index].bbox[3]),
            float(tables[index].bbox[2]),
            index,
        ),
    )
    ordered = list(indexes)
    for position, index in zip(valid_positions, sorted_valid_indexes, strict=True):
        ordered[position] = index  # 无 bbox 项保留抽取 ordinal；有效表仍按彼此真实坐标排序。
    return ordered


def _table_visual_has_valid_bbox(table: TableVisual) -> bool:
    """Return whether a table bbox can establish a finite physical position."""

    try:
        left, top, right, bottom = (float(value) for value in table.bbox)
    except (TypeError, ValueError):
        return False
    return bool(
        all(math.isfinite(value) for value in (left, top, right, bottom))
        and right > left
        and bottom > top
    )


def _same_page_unique_caption_order_is_preserved(
    old_tables: list[TableVisual],
    old_same_page: list[int],
    old_index: int,
    new_tables: list[TableVisual],
    new_same_page: list[int],
    new_index: int,
) -> bool | None:
    """Return LCS membership for a shared unique caption, else ``None``."""

    old_keys = [_table_visual_caption_key(old_tables[index]) for index in old_same_page]
    new_keys = [_table_visual_caption_key(new_tables[index]) for index in new_same_page]
    old_key = _table_visual_caption_key(old_tables[old_index])
    new_key = _table_visual_caption_key(new_tables[new_index])
    if not old_key or old_key != new_key:
        return None  # 无编号/不同表题仍交给物理 ordinal，不能用正文相似度跨表硬配。
    old_counts = Counter(key for key in old_keys if key)
    new_counts = Counter(key for key in new_keys if key)
    shared_unique = {
        key
        for key in old_counts.keys() & new_counts.keys()
        if old_counts[key] == 1 and new_counts[key] == 1
    }
    if old_key not in shared_unique:
        return None
    old_shared_order = [key for key in old_keys if key in shared_unique]
    new_shared_order = [key for key in new_keys if key in shared_unique]
    return old_key in _longest_common_caption_subsequence(
        old_shared_order,
        new_shared_order,
    )  # 只授权单调锚点；局部交叉留痕，但不会污染页面上其余稳定表格。


def _longest_common_caption_subsequence(
    old_keys: list[str],
    new_keys: list[str],
) -> set[str]:
    """Return one deterministic LCS of page-local unique caption identities."""

    lengths = [
        [0] * (len(new_keys) + 1)
        for _ in range(len(old_keys) + 1)
    ]
    for old_offset, old_key in enumerate(old_keys, start=1):
        for new_offset, new_key in enumerate(new_keys, start=1):
            if old_key == new_key:
                lengths[old_offset][new_offset] = lengths[old_offset - 1][new_offset - 1] + 1
            else:
                lengths[old_offset][new_offset] = max(
                    lengths[old_offset - 1][new_offset],
                    lengths[old_offset][new_offset - 1],
                )
    selected: set[str] = set()
    old_offset = len(old_keys)
    new_offset = len(new_keys)
    while old_offset and new_offset:
        if old_keys[old_offset - 1] == new_keys[new_offset - 1]:
            selected.add(old_keys[old_offset - 1])
            old_offset -= 1
            new_offset -= 1
        elif lengths[old_offset - 1][new_offset] >= lengths[old_offset][new_offset - 1]:
            old_offset -= 1
        else:
            new_offset -= 1
    return selected


def _pair_same_caption_table_groups(
    old_tables: list[TableVisual],
    new_tables: list[TableVisual],
    old_unused: set[int],
    new_unused: set[int],
    groups: list[_TableVisualGroup],
    *,
    old_sections: list[Section] | None = None,
    new_sections: list[Section] | None = None,
) -> None:
    """Pair exact captions only within the same local table occurrence."""

    old_unique_runs = _unique_caption_table_runs(old_tables, old_sections)  # 先形成单侧逻辑表，保留无题续页。
    new_unique_runs = _unique_caption_table_runs(new_tables, new_sections)  # 章节重编号不能拆散唯一编号表。
    for caption_key in sorted(old_unique_runs.keys() & new_unique_runs.keys()):
        old_indexes = old_unique_runs[caption_key]
        new_indexes = new_unique_runs[caption_key]
        if not all(index in old_unused for index in old_indexes):
            continue
        if not all(index in new_unused for index in new_indexes):
            continue
        old_context = _table_visual_section_context(
            old_tables[old_indexes[0]],
            old_sections or [],
        )  # 逻辑组首个有题页确定旧侧局部章节；续页仍参与后面的整组内容评分。
        new_context = _table_visual_section_context(
            new_tables[new_indexes[0]],
            new_sections or [],
        )  # 新侧同样以表题页定位，避免无题续页把上下文误判为空。
        crosses_proven_sections = bool(
            old_context and new_context and old_context != new_context
        )  # 只有两侧都能定位且章节不同时，唯一 caption 才需要额外内容授权。
        if (
            crosses_proven_sections
            and _table_visual_group_similarity(
                tuple(old_tables[index] for index in old_indexes),
                tuple(new_tables[index] for index in new_indexes),
            ) < _TABLE_PAIR_SIMILARITY_THRESHOLD
            and not _table_groups_have_relaxed_primary_identity_support(
                tuple(old_tables[index] for index in old_indexes),
                tuple(new_tables[index] for index in new_indexes),
            )
            and not _revision_record_groups_share_complete_baseline(
                tuple(old_tables[index] for index in old_indexes),
                tuple(new_tables[index] for index in new_indexes),
            )
        ):
            continue  # 跨 schema 第一列仍可证明同表；无共同参数身份时保留新增/删除。
        if _same_page_table_ordinals_differ(
            old_tables,
            old_indexes[0],
            new_tables,
            new_indexes[0],
        ):
            continue  # 同页物理顺序变化仍需留痕，不能由唯一表题掩盖。
        for old_index in old_indexes:
            old_unused.remove(old_index)  # 旧侧单页或续页全部纳入唯一表题逻辑组。
        for new_index in new_indexes:
            new_unused.remove(new_index)  # 新侧跨页续表与表题页一起配对。
        groups.append(
            _TableVisualGroup(
                tuple(old_tables[index] for index in old_indexes),
                tuple(new_tables[index] for index in new_indexes),
            )
        )

    _pair_exact_duplicate_caption_runs_by_content_lcs(
        old_tables,
        new_tables,
        old_unused,
        new_unused,
        groups,
        old_sections=old_sections,
        new_sections=new_sections,
    )

    old_by_key = _table_visual_indexes_by_caption_key(old_tables, old_sections)
    new_by_key = _table_visual_indexes_by_caption_key(new_tables, new_sections)
    for key in sorted(old_by_key.keys() & new_by_key.keys()):
        old_indexes = [index for index in old_by_key[key] if index in old_unused]  # 过滤掉前面已匹配的旧表。
        new_indexes = [index for index in new_by_key[key] if index in new_unused]  # 过滤掉前面已匹配的新表。
        if not old_indexes and not new_indexes:
            continue
        if (
            old_indexes
            and new_indexes
            and _same_page_table_ordinals_differ(
                old_tables,
                old_indexes[0],
                new_tables,
                new_indexes[0],
            )
        ):
            continue  # 编号/同名表在同页移动也必须留下变化，不能靠 caption 跨 ordinal 抵消。
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


def _pair_exact_duplicate_caption_runs_by_content_lcs(
    old_tables: list[TableVisual],
    new_tables: list[TableVisual],
    old_unused: set[int],
    new_unused: set[int],
    groups: list[_TableVisualGroup],
    *,
    old_sections: list[Section] | None,
    new_sections: list[Section] | None,
) -> None:
    """Anchor unchanged duplicate-caption runs before ordinal fallback pairing."""

    old_runs = _table_runs_by_caption_and_context(old_tables, old_sections)
    new_runs = _table_runs_by_caption_and_context(new_tables, new_sections)
    for base_key in sorted(old_runs.keys() & new_runs.keys()):
        old_available = [
            run for run in old_runs[base_key] if all(index in old_unused for index in run)
        ]
        new_available = [
            run for run in new_runs[base_key] if all(index in new_unused for index in run)
        ]
        if len(old_available) < 2 and len(new_available) < 2:
            continue
        old_content = [_exact_table_run_content_key(old_tables, run) for run in old_available]
        new_content = [_exact_table_run_content_key(new_tables, run) for run in new_available]
        for old_offset, new_offset in _longest_common_index_pairs(old_content, new_content):
            if not old_content[old_offset]:
                continue
            old_run = old_available[old_offset]
            new_run = new_available[new_offset]
            for index in old_run:
                old_unused.remove(index)
            for index in new_run:
                new_unused.remove(index)
            groups.append(
                _TableVisualGroup(
                    tuple(old_tables[index] for index in old_run),
                    tuple(new_tables[index] for index in new_run),
                )
            )

        old_remaining = [
            run for run in old_available if all(index in old_unused for index in run)
        ]
        new_remaining = [
            run for run in new_available if all(index in new_unused for index in run)
        ]
        old_revision_keys = [
            _revision_table_run_identity(old_tables, run) for run in old_remaining
        ]
        new_revision_keys = [
            _revision_table_run_identity(new_tables, run) for run in new_remaining
        ]
        old_key_counts = Counter(key for key in old_revision_keys if key)
        new_key_counts = Counter(key for key in new_revision_keys if key)
        old_unique_keys = [
            key
            if key and old_key_counts[key] == 1 and new_key_counts[key] == 1
            else ()
            for key in old_revision_keys
        ]
        new_unique_keys = [
            key
            if key and old_key_counts[key] == 1 and new_key_counts[key] == 1
            else ()
            for key in new_revision_keys
        ]
        for old_offset, new_offset in _longest_common_index_pairs(
            old_unique_keys,
            new_unique_keys,
        ):
            old_run = old_remaining[old_offset]
            new_run = new_remaining[new_offset]
            for index in old_run:
                old_unused.remove(index)
            for index in new_run:
                new_unused.remove(index)
            groups.append(
                _TableVisualGroup(
                    tuple(old_tables[index] for index in old_run),
                    tuple(new_tables[index] for index in new_run),
                )
            )


def _table_runs_by_caption_and_context(
    tables: list[TableVisual],
    sections: list[Section] | None,
) -> dict[str, list[list[int]]]:
    """Collect physical runs under one exact caption-and-section identity."""

    runs: dict[str, list[list[int]]] = {}
    for run_key, indexes in _table_visual_indexes_by_caption_key(tables, sections).items():
        base_key = run_key.rsplit("\x1frun:", 1)[0]
        runs.setdefault(base_key, []).append(indexes)
    return runs


def _exact_table_run_content_key(
    tables: list[TableVisual],
    indexes: list[int],
) -> tuple[str, ...]:
    """Return a lossless visible row sequence suitable only for exact anchoring."""

    return tuple(
        compact_inline(row)
        for index in indexes
        for row in tables[index].row_texts
        if compact_inline(row)
    )


def _revision_table_run_identity(
    tables: list[TableVisual],
    indexes: list[int],
) -> tuple[str, ...]:
    """Return complete unique Revision values for one proven revision-table run."""

    run_tables = tuple(tables[index] for index in indexes)
    if not any(_revision_record_table_key(table) for table in run_tables):
        return ()
    rows = _table_group_rows(run_tables)
    by_revision = _unique_revision_rows(rows)
    return tuple(by_revision) if by_revision else ()


def _longest_common_index_pairs(
    old_values: list[tuple[str, ...]],
    new_values: list[tuple[str, ...]],
) -> list[tuple[int, int]]:
    """Return deterministic monotonic index pairs for exact nonempty values."""

    lengths = [[0] * (len(new_values) + 1) for _ in range(len(old_values) + 1)]
    for old_offset, old_value in enumerate(old_values, start=1):
        for new_offset, new_value in enumerate(new_values, start=1):
            if old_value and old_value == new_value:
                lengths[old_offset][new_offset] = (
                    lengths[old_offset - 1][new_offset - 1] + 1
                )
            else:
                lengths[old_offset][new_offset] = max(
                    lengths[old_offset - 1][new_offset],
                    lengths[old_offset][new_offset - 1],
                )
    pairs: list[tuple[int, int]] = []
    old_offset = len(old_values)
    new_offset = len(new_values)
    while old_offset and new_offset:
        if (
            old_values[old_offset - 1]
            and old_values[old_offset - 1] == new_values[new_offset - 1]
        ):
            pairs.append((old_offset - 1, new_offset - 1))
            old_offset -= 1
            new_offset -= 1
        elif lengths[old_offset - 1][new_offset] >= lengths[old_offset][new_offset - 1]:
            old_offset -= 1
        else:
            new_offset -= 1
    return list(reversed(pairs))


def _table_visual_group_similarity(
    old_tables: tuple[TableVisual, ...],
    new_tables: tuple[TableVisual, ...],
) -> float:
    """Score one logical table run from all captions and all row identities."""

    old_captions = sorted(
        {
            caption
            for table in old_tables
            if (caption := _table_visual_caption_key(table))
        }
    )  # 续页可能重复表题，集合只保留逻辑表可见身份。
    new_captions = sorted(
        {
            caption
            for table in new_tables
            if (caption := _table_visual_caption_key(table))
        }
    )  # 新侧用相同规则汇总，页数变化不会改变 caption 权重。
    if (
        len(old_tables) == 1
        and len(new_tables) == 1
        and (not old_captions or not new_captions)
    ):
        return _table_visual_similarity(old_tables[0], new_tables[0])
        # 未编号单表没有 strict caption 集；展示实际标题+行身份分数，不能把空串/空串误报为 1.000。
    caption_similarity = difflib.SequenceMatcher(
        None,
        " ".join(old_captions),
        " ".join(new_captions),
        autojunk=False,
    ).ratio()  # 表题允许轻微抽取差异，但不能独自证明跨章节身份。
    old_rows = Counter(
        _table_row_pairing_key(row)
        for table in old_tables
        for row in table.row_texts
        if compact_inline(row)
    )  # 汇总旧逻辑表所有页面，避免只看第一页漏掉续表证据。
    new_rows = Counter(
        _table_row_pairing_key(row)
        for table in new_tables
        for row in table.row_texts
        if compact_inline(row)
    )  # 新逻辑表同样按稳定参数身份计数，数值修订不会破坏行配对。
    shared_row_count = sum((old_rows & new_rows).values())  # 多重集交集保留重复参数的真实出现次数。
    total_row_count = sum(old_rows.values()) + sum(new_rows.values())  # Dice 分母覆盖两侧整组行数。
    row_similarity = (
        (2.0 * shared_row_count / total_row_count)
        if total_row_count
        else 0.0
    )  # 没有任何结构化行时，caption 单独不足以授权跨章节配对。
    return min(caption_similarity, row_similarity)  # caption 与整组行身份必须同时过门，任一弱证据都不能被另一项补偿。


def _table_groups_have_relaxed_primary_identity_support(
    old_tables: tuple[TableVisual, ...],
    new_tables: tuple[TableVisual, ...],
) -> bool:
    """Accept a unique exact caption across schema drift only with shared row identities."""

    old_identities = Counter(
        identity
        for row in _table_group_rows(old_tables)
        if (identity := _table_row_relaxed_primary_identity(row))
    )
    new_identities = Counter(
        identity
        for row in _table_group_rows(new_tables)
        if (identity := _table_row_relaxed_primary_identity(row))
    )
    shared_count = sum((old_identities & new_identities).values())
    total_count = sum(old_identities.values()) + sum(new_identities.values())
    similarity = 2.0 * shared_count / total_count if total_count else 0.0
    return bool(
        shared_count >= 2
        and similarity >= _TABLE_PAIR_SIMILARITY_THRESHOLD
    )  # 一个相同行不足以越过章节上下文；两侧主身份 Dice 也必须达到既有门槛。


def _revision_record_groups_share_complete_baseline(
    old_tables: tuple[TableVisual, ...],
    new_tables: tuple[TableVisual, ...],
) -> bool:
    """Authorize revision-history growth only with one complete shared record."""

    if not (
        any(_revision_record_table_key(table) for table in old_tables)
        and any(_revision_record_table_key(table) for table in new_tables)
    ):
        return False
    old_records = Counter(
        record
        for row in _table_group_rows(old_tables)
        if (record := _revision_record_row_identity(row))
    )
    new_records = Counter(
        record
        for row in _table_group_rows(new_tables)
        if (record := _revision_record_row_identity(row))
    )
    return bool(old_records & new_records)


def _revision_record_row_identity(row: str) -> tuple[str, str, str] | None:
    """Return a complete Revision/Date/Description record, never a partial row."""

    fields = _table_row_fields(row)
    values = tuple(
        compact_inline(fields.get(label, "")).casefold()
        for label in ("revision", "date", "description")
    )
    return values if all(values) else None


def _table_row_relaxed_primary_identity(row: str) -> str:
    """Normalize an explicit identity and a continuation's Column 1 alike."""

    fields = _table_row_fields(row)
    label = _first_table_field(
        fields,
        ("parameter", "characteristic", "description", "label", "name", "symbol"),
    )
    if not label:
        label = fields.get("column 1", "")
    if compact_inline(label).casefold() in {
        "parameter",
        "characteristic",
        "description",
        "label",
        "name",
        "symbol",
    }:
        return ""
    return _table_row_text_key(label, field_label="symbol") if label else ""


def _unique_caption_table_runs(
    tables: list[TableVisual],
    sections: list[Section] | None,
) -> dict[str, list[int]]:
    """Return captions that identify exactly one logical run on one document side."""

    runs_by_caption: dict[str, list[list[int]]] = {}
    for group_key, indexes in _table_visual_indexes_by_caption_key(tables, sections).items():
        caption_key, separator, _context = group_key.partition("\x1fcontext:")
        if separator and caption_key:
            runs_by_caption.setdefault(caption_key, []).append(indexes)  # 相同表题跨独立章节会形成多条 run。
    return {
        caption_key: runs[0]
        for caption_key, runs in runs_by_caption.items()
        if len(runs) == 1
    }  # 只有单侧唯一逻辑出现才能跨章节漂移配对，重复表题仍由上下文区分。


def _table_visual_indexes_by_caption_key(
    tables: list[TableVisual],
    sections: list[Section] | None = None,
) -> dict[str, list[int]]:
    """Group only adjacent pages of one caption inside one section context."""

    grouped: dict[str, list[int]] = {}
    run_count_by_base: dict[str, int] = {}
    current_base = ""
    current_caption = ""
    current_context = ""
    current_group_key = ""
    current_page = -1
    current_index = -2
    for index, table in enumerate(tables):
        caption_key = _table_visual_caption_key(table)
        context_key = _table_visual_section_context(table, sections or [])
        if caption_key:
            base_key = f"{caption_key}\x1fcontext:{context_key}"
            continues_current = (
                caption_key == current_caption
                and index == current_index + 1
                and current_page < table.page_number <= current_page + 1
                and _table_contexts_support_adjacent_continuation(
                    current_context,
                    context_key,
                )
            )
            if not continues_current:
                run_count = run_count_by_base.get(base_key, 0) + 1
                run_count_by_base[base_key] = run_count
                current_group_key = f"{base_key}\x1frun:{run_count}"
            current_base = base_key
            current_caption = caption_key
            current_context = context_key
            current_page = table.page_number
        elif (
            table.is_continuation
            and current_group_key
            and index == current_index + 1
            and current_page < table.page_number <= current_page + 1
            and (
                (
                    bool(current_context and context_key)
                    and _table_contexts_support_adjacent_continuation(
                        current_context,
                        context_key,
                    )
                    and (
                        _table_rows_support_adjacent_continuation(
                            tables[current_index],
                            table,
                        )
                        or (
                            _table_geometry_supports_page_boundary_continuation(
                                tables[current_index],
                                table,
                            )
                            and (
                                _table_rows_support_sequential_identity_continuation(
                                    tables[current_index],
                                    table,
                                )
                                or _table_rows_support_repeated_header_continuation(
                                    tables[current_index],
                                    table,
                                )
                                or _table_rows_support_reliable_schema_boundary_continuation(
                                    tables[current_index],
                                    table,
                                    previous_context=current_context,
                                    current_context=context_key,
                                )
                            )
                        )
                    )
                )
                or (
                    not (current_context and context_key)
                    and (
                        _table_rows_support_adjacent_continuation(
                            tables[current_index],
                            table,
                        )
                        or (
                            _table_geometry_supports_page_boundary_continuation(
                                tables[current_index],
                                table,
                            )
                            and (
                                _table_rows_support_sequential_identity_continuation(
                                    tables[current_index],
                                    table,
                                )
                                or _table_rows_support_repeated_header_continuation(
                                    tables[current_index],
                                    table,
                                )
                            )
                        )
                    )
                )
            )
        ):
            current_page = table.page_number
            current_context = context_key or current_context  # 多页续表沿最新可定位章节继续验证下一页。
        else:
            current_base = ""
            current_caption = ""
            current_context = ""
            current_group_key = ""
        if current_group_key:
            grouped.setdefault(current_group_key, []).append(index)
        current_index = index
    return grouped


def _table_contexts_support_adjacent_continuation(
    previous_context: str,
    current_context: str,
) -> bool:
    """Require same context or a deep numeric sibling for a named continuation.

    A table can cross a subsection boundary that starts on its final page, but
    two adjacent top-level sections may independently reuse the same caption.
    Requiring at least two shared dotted-number components distinguishes those
    cases without relying on adjacency alone.
    """

    if previous_context == current_context:
        return True
    previous_number_path = _table_context_number_path(previous_context)
    current_number_path = _table_context_number_path(current_context)
    if not previous_number_path or not current_number_path:
        return False
    previous_leaf = _structural_number_token(previous_number_path.rsplit("/", 1)[-1])
    current_leaf = _structural_number_token(current_number_path.rsplit("/", 1)[-1])
    previous_parts = previous_leaf.split(".")
    current_parts = current_leaf.split(".")
    shared = 0
    for old_part, new_part in zip(previous_parts, current_parts):
        if old_part != new_part:
            break
        shared += 1
    return shared >= 2


def _table_context_number_path(context: str) -> str:
    """Extract the canonical numeric path from a semantic table context."""

    if not context.startswith("number:"):
        return ""
    numbered_context, _separator, _title = context.partition("\x1ftitle:")
    return numbered_context.removeprefix("number:")  # 标题参与身份判定，但章节连续性只比较结构编号。


def _table_rows_support_adjacent_continuation(
    previous_table: TableVisual,
    current_table: TableVisual,
) -> bool:
    """Accept an untitled continuation when its first row repeats the page boundary."""

    if not previous_table.row_texts or not current_table.row_texts:
        return False
    previous_boundary = _table_row_pairing_key(previous_table.row_texts[-1])
    current_boundary = _table_row_pairing_key(current_table.row_texts[0])
    return bool(
        previous_boundary and previous_boundary == current_boundary
    )  # 跨页重复边界行是内容连续证据；单独的 continuation 标志不再足够。


def _table_rows_support_sequential_identity_continuation(
    previous_table: TableVisual,
    current_table: TableVisual,
) -> bool:
    """Require a schema-sized, consecutive primary key across a page boundary."""

    if not previous_table.row_texts or not current_table.row_texts:
        return False
    previous_row = previous_table.row_texts[-1]
    current_row = current_table.row_texts[0]
    previous_fields = _table_row_fields(previous_row)
    current_fields = _table_row_fields(current_row)
    if not _table_row_schemas_support_headerless_continuation(previous_row, current_row):
        return False  # 纯几何和相同列数都不能越过不同字段语义。

    def primary_value(fields: dict[str, str]) -> str:
        value = _first_table_field(
            fields,
            ("parameter", "characteristic", "description", "label", "name", "symbol"),
        )
        return compact_inline(value or fields.get("column 1", ""))

    previous_match = re.fullmatch(r"(.+?)(\d+)", primary_value(previous_fields))
    current_match = re.fullmatch(r"(.+?)(\d+)", primary_value(current_fields))
    if previous_match is None or current_match is None:
        return False
    previous_prefix, previous_number = previous_match.groups()
    current_prefix, current_number = current_match.groups()
    return bool(
        re.search(r"[A-Za-z\u3400-\u4dbf\u4e00-\u9fff]", previous_prefix)
        and compact_inline(previous_prefix).casefold()
        == compact_inline(current_prefix).casefold()
        and int(current_number) == int(previous_number) + 1
    )  # P2→P3 之类连续记录可佐证续页；普通独立表名不会仅靠页边位置被吸收。


def _table_rows_support_repeated_header_continuation(
    previous_table: TableVisual,
    current_table: TableVisual,
) -> bool:
    """Recognize a headerless page whose first generic row repeats the prior schema."""

    if not previous_table.row_texts or not current_table.row_texts:
        return False
    previous_entries = _table_row_field_entries(previous_table.row_texts[-1])
    current_entries = _table_row_field_entries(current_table.row_texts[0])
    if len(previous_entries) < 3 or len(previous_entries) != len(current_entries):
        return False
    current_labels = tuple(entry[2] for entry in current_entries)
    if current_labels != tuple(
        f"column {index}" for index in range(1, len(current_entries) + 1)
    ):
        return False
    aliases = {
        "units": "unit",
        "conditions": "condition",
        "notes": "note",
        "remarks": "remark",
        "descriptions": "description",
    }
    previous_schema = [
        aliases.get(entry[2], entry[2])
        for entry in previous_entries
    ]
    repeated_schema: list[str] = []
    for entry in current_entries:
        repeated_label = compact_inline(
            entry[3].replace("↵", " ").replace("\\n", " ")
        ).casefold()
        repeated_schema.append(aliases.get(repeated_label, repeated_label))
    # 表格 codec 用 ↵ 保留单元格内换行；表头身份比较只把它当空白，不删除数字等未知残片。
    matches = sum(
        old_label == repeated_label
        for old_label, repeated_label in zip(
            previous_schema,
            repeated_schema,
            strict=True,
        )
    )
    return bool(
        previous_schema[0] == repeated_schema[0]
        and matches >= 3
        and matches / len(previous_schema) >= 0.75
    )  # 页边几何之外还须重复身份列和至少四分之三 schema；孤立无标题表不能靠位置冒充续表。


def _table_rows_support_reliable_schema_boundary_continuation(
    previous_table: TableVisual,
    current_table: TableVisual,
    *,
    previous_context: str,
    current_context: str,
) -> bool:
    """Accept an exact explicit schema only across a proven heading boundary.

    A heading printed below a continued table can make the two physical pieces
    inherit adjacent subsection contexts.  Same-context tables remain rejected:
    otherwise page-edge geometry plus a common two-column schema could absorb an
    independent untitled table.
    """

    if not (
        previous_context
        and current_context
        and previous_context != current_context
        and previous_table.content_fully_represented
        and current_table.content_fully_represented
        and previous_table.row_alignment_reliable
        and current_table.row_alignment_reliable
        and previous_table.row_texts
        and current_table.row_texts
    ):
        return False
    previous_labels = tuple(
        entry[2]
        for entry in _table_row_field_entries(previous_table.row_texts[-1])
    )
    current_labels = tuple(
        entry[2]
        for entry in _table_row_field_entries(current_table.row_texts[0])
    )
    if len(previous_labels) < 3 or previous_labels != current_labels:
        return False
    generic_schema = tuple(
        f"column {index}" for index in range(1, len(previous_labels) + 1)
    )
    # 显式同 schema、逐字符守恒、可靠行归属、跨小节页边四项缺一不可。
    return previous_labels != generic_schema


def _table_row_schemas_support_headerless_continuation(
    previous_row: str,
    current_row: str,
) -> bool:
    """Require equal field semantics or one complete positional Column-N schema."""

    aliases = {
        "units": "unit",
        "conditions": "condition",
        "notes": "note",
        "remarks": "remark",
        "descriptions": "description",
    }

    def labels(row: str) -> tuple[str, ...]:
        return tuple(
            aliases.get(normalized_label, normalized_label)
            for _index, _display, normalized_label, _value in _table_row_field_entries(row)
        )

    def is_complete_generic(candidate: tuple[str, ...]) -> bool:
        return bool(
            len(candidate) >= 2
            and candidate
            == tuple(f"column {index}" for index in range(1, len(candidate) + 1))
        )

    previous_labels = labels(previous_row)
    current_labels = labels(current_row)
    if len(previous_labels) < 2 or len(previous_labels) != len(current_labels):
        return False
    return bool(
        previous_labels == current_labels
        or is_complete_generic(previous_labels)
        or is_complete_generic(current_labels)
    )  # headerless continuation may expose only Column N；两个显式但不同的 schema 绝不等价。


def _table_geometry_supports_page_boundary_continuation(
    previous_table: TableVisual,
    current_table: TableVisual,
) -> bool:
    """Recognize one horizontally aligned table crossing a physical page edge."""

    if (
        not previous_table.row_texts
        or not current_table.row_texts
        or previous_table.page_bbox is None
        or current_table.page_bbox is None
    ):
        return False
    previous_page_left, previous_page_top, previous_page_right, previous_page_bottom = previous_table.page_bbox
    current_page_left, current_page_top, current_page_right, current_page_bottom = current_table.page_bbox
    previous_page_width = previous_page_right - previous_page_left
    current_page_width = current_page_right - current_page_left
    previous_page_height = previous_page_bottom - previous_page_top
    current_page_height = current_page_bottom - current_page_top
    if min(
        previous_page_width,
        current_page_width,
        previous_page_height,
        current_page_height,
    ) <= 0.0:
        return False
    previous_x0 = (previous_table.bbox[0] - previous_page_left) / previous_page_width
    previous_x1 = (previous_table.bbox[2] - previous_page_left) / previous_page_width
    current_x0 = (current_table.bbox[0] - current_page_left) / current_page_width
    current_x1 = (current_table.bbox[2] - current_page_left) / current_page_width
    previous_width = previous_x1 - previous_x0
    current_width = current_x1 - current_x0
    if previous_width <= 0.0 or current_width <= 0.0:
        return False
    overlap_width = min(previous_x1, current_x1) - max(previous_x0, current_x0)
    if overlap_width <= 0.0:
        return False
    horizontal_coverage = overlap_width / min(previous_width, current_width)
    width_ratio = min(previous_width, current_width) / max(previous_width, current_width)
    previous_bottom_gap = (
        previous_page_bottom - previous_table.bbox[3]
    ) / previous_page_height
    current_top_gap = (
        current_table.bbox[1] - current_page_top
    ) / current_page_height
    return bool(
        0.0 <= previous_bottom_gap <= _TABLE_PAGE_EDGE_MAX_FRACTION
        and 0.0 <= current_top_gap <= _TABLE_PAGE_EDGE_MAX_FRACTION
        and horizontal_coverage >= 0.9
        and width_ratio >= 0.85
    )  # 必须同时靠近真实上/下页边且水平对齐；仅有两个 table bbox 的上下顺序不是跨页证据。


def _table_visual_section_context(table: TableVisual, sections: list[Section]) -> str:
    """Return the most specific enclosing section identity available for a table."""

    candidates = [
        section
        for section in sections
        if section.start_page <= table.page_number <= section.end_page
    ]
    if not candidates:
        return ""
    section = max(
        candidates,
        key=lambda item: (len(item.number_path), item.level, item.start_page),
    )
    if section.number_path:
        number_context = "number:" + "/".join(
            _canonical_table_section_number_path(section.number_path)
        )
        title_context = compact_inline(section.title).casefold()
        return f"{number_context}\x1ftitle:{title_context}"  # 同号异名章节不能被压缩成同一表格上下文。
    return "heading:" + compact_inline(section.location).casefold()


def _canonical_table_section_number_path(number_path: tuple[str, ...]) -> tuple[str, ...]:
    """Drop ancestors already encoded by the most specific dotted number.

    Extractor revisions can newly recognize a top-level ``32`` while both
    versions still agree on leaf ``32.2.4``.  Such redundant parents must not
    split an exact-caption table into add/delete.  Non-redundant Part/Annex
    context remains so locally reused table numbers do not cross-pair.
    """

    normalized = tuple(
        compact_inline(part).casefold()
        for part in number_path
        if compact_inline(part)
    )
    if len(normalized) <= 1:
        return normalized
    leaf = _structural_number_token(normalized[-1])
    kept = [
        part
        for part in normalized[:-1]
        if not _structural_number_is_ancestor(_structural_number_token(part), leaf)
    ]
    return tuple([*kept, normalized[-1]])


def _structural_number_token(value: str) -> str:
    """Normalize named numeric parents for prefix comparison only."""

    candidate = re.sub(
        r"^(?:chapter|section|clause)\s+",
        "",
        compact_inline(value).casefold(),
    )
    annex = re.fullmatch(r"(?:annex|appendix)\s+([a-z0-9]+)", candidate)
    return annex.group(1) if annex else candidate


def _structural_number_is_ancestor(parent: str, leaf: str) -> bool:
    """Return whether a parent number is already encoded by a dotted leaf."""

    if not parent or not leaf or parent == leaf:
        return False
    return leaf.startswith(parent + ".")


def _table_visual_caption_key(table: TableVisual) -> str:
    """Return an exact pairing key for repeated pages of the same named table."""

    revision_record_key = _revision_record_table_key(table)
    if revision_record_key:
        return revision_record_key
    title = _normalized_numbered_table_caption(table.title).casefold()
    if not title:
        return ""  # 无编号表或续页缺表题时继续交给后面的保守 fuzzy 配对。
    return re.sub(r"\s+", " ", title).strip()


def _revision_record_table_key(table: TableVisual) -> str:
    """Identify an unnumbered revision record from title plus all three fields."""

    title = compact_inline(table.title).casefold()
    if not _looks_like_revision_table_context_title(title):
        return ""
    if not any(
        re.search(r"(?i)(?:^|\|)\s*revision\s*=", row)
        and re.search(r"(?i)(?:^|\|)\s*date\s*=", row)
        and re.search(r"(?i)(?:^|\|)\s*description\s*=", row)
        for row in table.row_texts
    ):
        return ""
    # 引导句属于可变显示标题，不是表身份；章节上下文、唯一 run 与物理 ordinal 继续阻止跨表串配。
    return "revision-record:revision|date|description"


def _looks_like_revision_table_context_title(value: str) -> bool:
    """Require an explicit unnumbered table introduction, not a generic caption."""

    return bool(
        re.search(
            r"(?i)\b(?:in\s+the\s+table\s+below|table\s+(?:below|following)"
            r"|(?:the\s+)?following\s+table)\b"
            r"|\b(?:revision|change|version)\s+(?:history|record|log)\b"
            r"|修订(?:历史|记录)|变更(?:历史|记录)|下表",
            compact_inline(value),
        )
    )


_NUMBERED_TABLE_CAPTION_RE = re.compile(
    rf"(?i)^(?P<prefix>table\s+)(?P<number>\d+(?:\s*{TABLE_NUMBER_DASH_CLASS}\s*\d+)?)"
    rf"(?!\w|\s*{TABLE_NUMBER_DASH_CLASS}|\s*/|\s*\.(?=\S))"
)


def _normalized_numbered_table_caption(value: str) -> str:
    """Normalize only a strict table-number prefix, leaving descriptor punctuation intact."""

    title = compact_inline(value)
    match = _NUMBERED_TABLE_CAPTION_RE.match(title)
    if match is not None:
        number = normalize_table_number_dashes(match.group("number"))
        return f"{match.group('prefix')}{number}{title[match.end():]}"
    return title if re.match(r"^表\s*\d+(?!\w)", title) else ""


def _table_visual_caption_descriptor_key(table: TableVisual) -> str:
    """Return a stable descriptive caption after removing only the table number."""

    caption = _table_visual_caption_key(table)
    if not caption:
        return ""
    descriptor = re.sub(
        r"^(?:table\s+\d+(?:-\d+)?|表\s*\d+)\s*[.：:\-–—]*\s*",
        "",
        caption,
        flags=re.I,
    ).strip()
    words = re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", descriptor, flags=re.I)
    if len(descriptor) < 12 or len(words) < 3:
        return ""  # “Parameters”一类短标题不足以越过章节上下文。
    return descriptor


def _unique_exact_table_captions(
    old_tables: list[TableVisual],
    new_tables: list[TableVisual],
) -> set[str]:
    """Return exact numbered captions observed once on each document side."""

    old_counts = Counter(
        key for table in old_tables if (key := _table_visual_caption_key(table))
    )
    new_counts = Counter(
        key for table in new_tables if (key := _table_visual_caption_key(table))
    )
    return {
        key
        for key in old_counts.keys() & new_counts.keys()
        if old_counts[key] == 1 and new_counts[key] == 1
    }


def _table_visual_number_identity(table: TableVisual) -> tuple[str, int] | None:
    """Return a numbered-table family and terminal ordinal for shift evidence."""

    caption = _table_visual_caption_key(table)
    match = re.match(
        r"^(?:table\s+|表\s*)(\d+(?:-\d+)+)\b",
        caption,
        flags=re.I,
    )
    if not match:
        return None
    parts = match.group(1).split("-")
    if len(parts) < 2:
        return None
    return "-".join(parts[:-1]), int(parts[-1])


def _table_descriptive_renumbering_key(
    old_table: TableVisual,
    new_table: TableVisual,
) -> tuple[str, int] | None:
    """Return one exact descriptor-backed table-number shift candidate."""

    old_descriptor = _table_visual_caption_descriptor_key(old_table)
    new_descriptor = _table_visual_caption_descriptor_key(new_table)
    old_identity = _table_visual_number_identity(old_table)
    new_identity = _table_visual_number_identity(new_table)
    if (
        not old_descriptor
        or old_descriptor != new_descriptor
        or old_identity is None
        or new_identity is None
        or old_identity[0] != new_identity[0]
    ):
        return None
    shift = new_identity[1] - old_identity[1]
    if shift == 0:
        return None
    return old_identity[0], shift


def _supported_table_renumberings(
    old_tables: list[TableVisual],
    new_tables: list[TableVisual],
) -> set[tuple[str, int]]:
    """Accept cross-context renumbering only when two unique captions prove a shift."""

    old_by_descriptor: dict[str, list[TableVisual]] = {}
    new_by_descriptor: dict[str, list[TableVisual]] = {}
    for table in old_tables:
        descriptor = _table_visual_caption_descriptor_key(table)
        if descriptor:
            old_by_descriptor.setdefault(descriptor, []).append(table)
    for table in new_tables:
        descriptor = _table_visual_caption_descriptor_key(table)
        if descriptor:
            new_by_descriptor.setdefault(descriptor, []).append(table)

    descriptors_by_shift: dict[tuple[str, int], set[str]] = {}
    for descriptor in old_by_descriptor.keys() & new_by_descriptor.keys():
        if len(old_by_descriptor[descriptor]) != 1 or len(new_by_descriptor[descriptor]) != 1:
            continue
        key = _table_descriptive_renumbering_key(
            old_by_descriptor[descriptor][0],
            new_by_descriptor[descriptor][0],
        )
        if key is not None:
            descriptors_by_shift.setdefault(key, set()).add(descriptor)
    return {
        key
        for key, descriptors in descriptors_by_shift.items()
        if len(descriptors) >= 2
    }


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
    row_identities = sorted(
        _table_row_pairing_key(row)
        for row in table.row_texts
        if compact_inline(row)
    )
    rows = " ".join(row_identities[:6])  # 排序后的行身份不受字段顺序、数据行顺序或数值变化影响。
    return compact_inline(f"{title} {rows}").casefold()


def _render_table_change_html(
    index: int,
    change: TableChange,
) -> str:
    """Render one changed logical table with auditable pairing metadata."""

    title = _table_change_title(change)
    old_shot = _render_table_shot_group("旧版截图", change.old_tables)
    new_shot = _render_table_shot_group("新版截图", change.new_tables)
    rows_html = _render_table_row_summary(change)
    label = _CHANGE_LABELS.get(change.change_type, change.change_type)
    similarity = (
        f" · 配对相似度 {change.similarity:.3f}"
        if change.old_tables and change.new_tables
        else ""
    )
    return f"""
        <div class="table-visual-card" id="table-change-{index}">
          <h3><span class="badge badge-{change.change_type}">{_escape(label)}</span>{_escape(title)}</h3>
          <div class="table-status">旧表：{_escape(_table_side_description(change.old_tables))}<br>
          新表：{_escape(_table_side_description(change.new_tables))}{_escape(similarity)}</div>
          <div class="table-shot-grid">{old_shot}{new_shot}</div>
          {rows_html}
        </div>
    """


def _table_change_title(change: TableChange) -> str:
    """Build a concise title while retaining explicit old/new caption mapping."""

    table = next(iter(change.new_tables or change.old_tables), None)
    if table is None:
        return "未命名表格"
    title = table.title or ("跨页表格续段" if table.is_continuation else "")
    if title:
        return title
    return f"第 {table.page_number} 页表格 {table.table_number}"


def _table_side_description(tables: tuple[TableVisual, ...]) -> str:
    """Return captions and source pages for one side of a table change."""

    if not tables:
        return "无对应表格"
    titles = _unique_table_titles(tables)
    title_text = " / ".join(titles) if titles else "无表题续段"
    pages = ", ".join(str(table.page_number) for table in tables)
    return f"{title_text}（页 {pages}）"


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
    text_backed = table.ocr_status == "text_backed_exact_match"
    grid_summary = (
        "行列网格：未验证"
        if text_backed
        else _display_table_grid_summary(table.grid_summary)
    )
    image_html = (
        f'<img alt="{_escape(caption)}" src="{table.image_data_uri}">'
        if table.image_data_uri
        else (
            '<div class="snippet">无截图：扁平文字精确匹配，行列边界未验证</div>'
            if text_backed
            else '<div class="snippet">无截图：仅使用结构化表格行摘要</div>'
        )
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
    change: TableChange,
) -> str:
    """Render compact HTML from precomputed row-change facts."""

    source_rows = list(change.row_changes)
    if not source_rows:
        message = (
            "表题/表号发生变化，未检测到行级内容变化"
            if change.caption_changed
            else "未抽取到可靠行级文字，请结合截图复核"
        )
        kind = "表题/表号变化" if change.caption_changed else "需截图复核"
        source_rows = [TableRowChange(message, "", "", kind)]
    review_rows = [row for row in source_rows if row.change_type == "需人工复核"]
    material_rows = [row for row in source_rows if row.change_type != "需人工复核"]
    row_budget = 20
    material_budget = (
        row_budget - 1
        if material_rows and review_rows
        else row_budget
    )
    visible_material_rows = material_rows[:material_budget]
    review_budget = row_budget - len(visible_material_rows)
    visible_source_rows = [
        *visible_material_rows,
        *review_rows[:review_budget],
    ]  # 实质变化至少保留一席；两类同时存在时也固定保留一条不确定性证据。
    visible_rows = [_render_table_row_change(row) for row in visible_source_rows]
    omitted_count = max(0, len(source_rows) - len(visible_source_rows))
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


def _table_row_changes(
    old_tables: tuple[TableVisual, ...],
    new_tables: tuple[TableVisual, ...],
) -> list[TableRowChange]:
    """Return complete row findings matched by identity rather than row position."""

    old_rows = _table_group_rows(old_tables)
    new_rows = _table_group_rows(new_tables)
    old_rows, new_rows = _remove_same_position_descriptor_reflows(
        old_rows,
        new_rows,
    )
    collision_resolution = _table_pua_identity_collision_resolution(
        old_tables,
        new_tables,
        old_rows,
        new_rows,
    )
    review_changes: list[TableRowChange] = []
    if collision_resolution is not None:
        review_changes, old_rows, new_rows = collision_resolution
    wrap_resolution = _table_descriptor_wrap_redistribution_resolution(
        old_rows,
        new_rows,
    )
    if wrap_resolution is not None:
        wrap_reviews, old_rows, new_rows = wrap_resolution
        review_changes.extend(wrap_reviews)
    revision_changes = _revision_record_row_changes(
        old_tables,
        new_tables,
        old_rows,
        new_rows,
    )
    if revision_changes is not None:
        return [*review_changes, *revision_changes]
    if not _table_group_has_order_independent_parameter_identity(
        old_tables,
        new_tables,
        old_rows,
        new_rows,
    ):
        return [
            *review_changes,
            *_ordered_table_row_changes(old_rows, new_rows),
        ]
    old_by_key = _table_rows_by_pairing_key(old_rows)
    new_by_key = _table_rows_by_pairing_key(new_rows)
    ordered_keys = list(dict.fromkeys([*old_by_key, *new_by_key]))
    changes: list[TableRowChange] = list(review_changes)
    for key in ordered_keys:
        old_remaining, new_remaining = _remove_equal_table_rows(
            old_by_key.get(key, []),
            new_by_key.get(key, []),
        )
        paired_count = min(len(old_remaining), len(new_remaining))
        for index in range(paired_count):
            old_row = old_remaining[index]
            new_row = new_remaining[index]
            kind = _table_structured_diff_kind(old_row, new_row)
            if kind != "无变化":
                changes.append(_make_table_row_change(old_row, new_row, kind))
        for old_row in old_remaining[paired_count:]:
            changes.append(_make_table_row_change(old_row, "", "旧表删除行"))
        for new_row in new_remaining[paired_count:]:
            changes.append(_make_table_row_change("", new_row, "新表新增行"))
    return changes


def _remove_same_position_descriptor_reflows(
    old_rows: list[str],
    new_rows: list[str],
) -> tuple[list[str], list[str]]:
    """Consume rows whose sole difference is a line break inside one identity cell."""

    if len(old_rows) != len(new_rows):
        return old_rows, new_rows
    consumed: set[int] = set()
    for index, (old_row, new_row) in enumerate(zip(old_rows, new_rows, strict=True)):
        old_fact = _table_descriptor_wrap_fact(old_row)
        new_fact = _table_descriptor_wrap_fact(new_row)
        if old_fact is None or new_fact is None:
            continue
        old_label, old_non_identity, old_identity_key, old_value = old_fact
        new_label, new_non_identity, new_identity_key, new_value = new_fact
        if (
            old_label != new_label
            or old_non_identity != new_non_identity
            or old_identity_key == new_identity_key
            or "↵" not in f"{old_value}{new_value}"
        ):
            continue
        old_single_line = _table_row_single_line_text_key(
            old_value.replace("↵", " "),
            field_label=old_label,
        )
        new_single_line = _table_row_single_line_text_key(
            new_value.replace("↵", " "),
            field_label=new_label,
        )
        if old_single_line == new_single_line:
            consumed.add(index)
    if not consumed:
        return old_rows, new_rows
    return (
        [row for index, row in enumerate(old_rows) if index not in consumed],
        [row for index, row in enumerate(new_rows) if index not in consumed],
    )


def _table_descriptor_wrap_redistribution_resolution(
    old_rows: list[str],
    new_rows: list[str],
) -> tuple[list[TableRowChange], list[str], list[str]] | None:
    """Collapse wrap-only adjacent identity shifts into one explicit review fact.

    Without cell baselines, redistributing the same words across adjacent
    Parameter cells cannot be proven equal because it may also change which
    symbol/value owns a word.  It is nevertheless not evidence for several
    additions/deletions when every non-identity field is unchanged.  Preserve
    that ambiguity as one review row instead of reporting a material diff wall.
    """

    if len(old_rows) != len(new_rows) or len(old_rows) < 2:
        return None
    old_facts = [_table_descriptor_wrap_fact(row) for row in old_rows]
    new_facts = [_table_descriptor_wrap_fact(row) for row in new_rows]
    consumed: set[int] = set()
    reviews: list[TableRowChange] = []
    index = 0
    while index < len(old_rows):
        old_fact = old_facts[index]
        new_fact = new_facts[index]
        if (
            old_fact is None
            or new_fact is None
            or old_fact[0] != new_fact[0]
            or old_fact[1] != new_fact[1]
            or old_fact[2] == new_fact[2]
        ):
            index += 1
            continue
        start = index
        while index < len(old_rows):
            old_candidate = old_facts[index]
            new_candidate = new_facts[index]
            if (
                old_candidate is None
                or new_candidate is None
                or old_candidate[0] != new_candidate[0]
                or old_candidate[1] != new_candidate[1]
                or old_candidate[2] == new_candidate[2]
            ):
                break
            index += 1
        if index - start < 2:
            continue
        old_values = [old_facts[row_index][3] for row_index in range(start, index)]  # type: ignore[index]
        new_values = [new_facts[row_index][3] for row_index in range(start, index)]  # type: ignore[index]
        field_label = old_facts[start][0]  # type: ignore[index]
        old_joined = _table_row_single_line_text_key(
            " ".join(old_values).replace("↵", " "),
            field_label=field_label,
        )
        new_joined = _table_row_single_line_text_key(
            " ".join(new_values).replace("↵", " "),
            field_label=field_label,
        )
        if old_joined != new_joined:
            continue
        consumed.update(range(start, index))
        reviews.append(
            TableRowChange(
                item="项目名称换行归属需复核",
                old_value=" / ".join(old_values),
                new_value=" / ".join(new_values),
                change_type="需人工复核",
            )
        )
    if not consumed:
        return None
    return (
        reviews,
        [row for row_index, row in enumerate(old_rows) if row_index not in consumed],
        [row for row_index, row in enumerate(new_rows) if row_index not in consumed],
    )


def _table_descriptor_wrap_fact(
    row: str,
) -> tuple[str, tuple[tuple[int, str, str], ...], str, str] | None:
    """Return identity label, non-identity facts, identity key, and display text."""

    cells = _table_row_cells_for_display(row)
    entries = _table_row_field_entries(row)
    if len(entries) != len(cells):
        return None  # 无标签单元格的行归属不能由字段投影证明。
    identity_labels = {"parameter", "characteristic", "description", "label", "name"}
    identity_entries = [entry for entry in entries if entry[2] in identity_labels]
    if len(identity_entries) != 1:
        return None
    identity_column, _display_label, identity_label, identity_value = identity_entries[0]
    non_identity = tuple(
        (
            column_index,
            normalized_label,
            _table_row_text_key(value, field_label=normalized_label),
        )
        for column_index, _label, normalized_label, value in entries
        if column_index != identity_column
    )
    return (
        identity_label,
        non_identity,
        _table_row_text_key(identity_value, field_label=identity_label),
        identity_value,
    )


def _table_pua_identity_collision_resolution(
    old_tables: tuple[TableVisual, ...],
    new_tables: tuple[TableVisual, ...],
    old_rows: list[str],
    new_rows: list[str],
) -> tuple[list[TableRowChange], list[str], list[str]] | None:
    """Consume reader-equal pairs only inside a proven known-PUA identity collision."""

    if not old_rows or not new_rows:
        return None
    observed_text = " ".join(
        [
            *(table.title for table in (*old_tables, *new_tables)),
            *old_rows,
            *new_rows,
        ]
    )
    if re.search(
        r"(?i)\b(?:first[- ]match|top\s+to\s+bottom|policy|priority|precedence|rules?|sequence|ordered|ordering|rank|step)\b",
        observed_text,
    ):
        return None

    def identity_facts(rows: list[str]) -> dict[int, tuple[str, str, str]]:
        facts: dict[int, tuple[str, str, str]] = {}
        for row_index, row in enumerate(rows):
            fields = _table_row_fields(row)
            field_names = set(fields)
            if field_names & {
                "action",
                "command",
                "condition",
                "match",
                "order",
                "priority",
                "rank",
                "rule",
                "sequence",
                "step",
            }:
                continue  # 顺序/条件语义只排除本行进入碰撞簇；无关行仍由后续 material 路径比较。
            if not field_names & {
                "limit",
                "max",
                "maximum",
                "min",
                "minimum",
                "typ",
                "typical",
                "unit",
                "units",
            }:
                continue
            field_label = ""
            label = _first_table_field(
                fields,
                ("parameter", "characteristic", "description", "label", "name"),
            )
            if not label:
                label = fields.get("symbol", "")
                if label:
                    field_label = "symbol"
            if not label:
                continue
            raw_key = _table_row_text_key(label, field_label=field_label)
            mapped_key = _table_row_text_key(
                readable_symbol_font_glyphs(label),
                field_label=field_label,
            )
            facts[row_index] = (label, raw_key, mapped_key)
        return facts

    old_facts = identity_facts(old_rows)
    new_facts = identity_facts(new_rows)
    old_indexes_by_mapped: dict[str, list[int]] = {}
    new_indexes_by_mapped: dict[str, list[int]] = {}
    for index, (_label, _raw_key, mapped_key) in old_facts.items():
        old_indexes_by_mapped.setdefault(mapped_key, []).append(index)
    for index, (_label, _raw_key, mapped_key) in new_facts.items():
        new_indexes_by_mapped.setdefault(mapped_key, []).append(index)

    collision_keys: list[str] = []
    for mapped_key in old_indexes_by_mapped:
        if mapped_key not in new_indexes_by_mapped:
            continue
        old_indexes = old_indexes_by_mapped[mapped_key]
        new_indexes = new_indexes_by_mapped[mapped_key]
        if max(len(old_indexes), len(new_indexes)) < 2:
            continue
        old_raw_keys = [old_facts[index][1] for index in old_indexes]
        new_raw_keys = [new_facts[index][1] for index in new_indexes]
        if (
            len(old_raw_keys) != len(set(old_raw_keys))
            or len(new_raw_keys) != len(set(new_raw_keys))
        ):
            continue  # 该碰撞簇内普通重复身份没有一对一事实，不能借 PUA 规则猜配。
        labels = [
            *(old_facts[index][0] for index in old_indexes),
            *(new_facts[index][0] for index in new_indexes),
        ]
        if any(
            "\ue000" <= character <= "\uf8ff"
            and readable_symbol_font_glyphs(character) != character
            for label in labels
            for character in label
        ):
            collision_keys.append(mapped_key)
    if not collision_keys:
        return None

    consumed_old: set[int] = set()
    consumed_new: set[int] = set()
    reviews: list[TableRowChange] = []
    for mapped_identity in collision_keys:
        old_indexes = old_indexes_by_mapped[mapped_identity]
        new_indexes = new_indexes_by_mapped[mapped_identity]
        collision_pairs: list[tuple[str, str]] = []

        new_by_raw_row: dict[str, list[int]] = {}
        for new_index in new_indexes:
            new_by_raw_row.setdefault(
                _table_row_display_key(new_rows[new_index]),
                [],
            ).append(new_index)
        for old_index in old_indexes:
            raw_key = _table_row_display_key(old_rows[old_index])
            candidates = new_by_raw_row.get(raw_key, [])
            new_index = next(
                (index for index in candidates if index not in consumed_new),
                None,
            )
            if new_index is not None:
                consumed_old.add(old_index)
                consumed_new.add(new_index)

        old_by_mapped_row: dict[str, list[int]] = {}
        new_by_mapped_row: dict[str, list[int]] = {}
        for old_index in old_indexes:
            if old_index not in consumed_old:
                old_by_mapped_row.setdefault(
                    _table_row_display_key(
                        readable_symbol_font_glyphs(old_rows[old_index])
                    ),
                    [],
                ).append(old_index)
        for new_index in new_indexes:
            if new_index not in consumed_new:
                new_by_mapped_row.setdefault(
                    _table_row_display_key(
                        readable_symbol_font_glyphs(new_rows[new_index])
                    ),
                    [],
                ).append(new_index)
        for mapped_row_key in old_by_mapped_row:
            if mapped_row_key not in new_by_mapped_row:
                continue
            old_candidates = old_by_mapped_row[mapped_row_key]
            new_candidates = new_by_mapped_row[mapped_row_key]
            for old_index, new_index in zip(
                old_candidates,
                new_candidates,
                strict=False,
            ):
                consumed_old.add(old_index)
                consumed_new.add(new_index)
                collision_pairs.append(
                    (old_rows[old_index], new_rows[new_index])
                )
        if collision_pairs:
            mapped_pua = list(
                dict.fromkeys(
                    character
                    for pair in collision_pairs
                    for row in pair
                    for character in row
                    if "\ue000" <= character <= "\uf8ff"
                    and readable_symbol_font_glyphs(character) != character
                )
            )
            visible_characters = list(
                dict.fromkeys(
                    readable_symbol_font_glyphs(character)
                    for character in mapped_pua
                )
            )
            pua_codes = ", ".join(
                f"U+{ord(character):04X}" for character in mapped_pua
            )
            unicode_codes = ", ".join(
                f"U+{ord(character):04X}" for character in visible_characters
            )
            reviews.append(
                TableRowChange(
                    item=(
                        "行身份字体编码冲突"
                        f"（{pua_codes} 与 {unicode_codes} 的语义未验证）"
                    ),
                    old_value="；".join(
                        compact_inline(old_row)
                        for old_row, _new_row in collision_pairs
                    ),
                    new_value="；".join(
                        compact_inline(new_row)
                        for _old_row, new_row in collision_pairs
                    ),
                    change_type="需人工复核",
                )
            )

    if not consumed_old and not consumed_new:
        return None
    return (
        reviews,
        [row for index, row in enumerate(old_rows) if index not in consumed_old],
        [row for index, row in enumerate(new_rows) if index not in consumed_new],
    )


def _table_group_has_order_independent_parameter_identity(
    old_tables: tuple[TableVisual, ...],
    new_tables: tuple[TableVisual, ...],
    old_rows: list[str],
    new_rows: list[str],
) -> bool:
    """Allow multiset matching only with positive parameter-table evidence."""

    observed_text = " ".join(
        [
            *(table.title for table in (*old_tables, *new_tables)),
            *old_rows,
            *new_rows,
        ]
    )
    if re.search(
        r"(?i)\b(?:first[- ]match|top\s+to\s+bottom|policy|priority|precedence|rules?|sequence|ordered|ordering|rank|step)\b",
        observed_text,
    ):
        return False
    if (
        _table_groups_mix_generic_and_explicit_schema(old_rows, new_rows)
        and _table_groups_have_relaxed_primary_identity_support(
            old_tables,
            new_tables,
        )
    ):
        return True  # 同一参数表跨页丢失表头时，Column 1 与 Parameter 仍按唯一主身份配对。

    def unique_parameter_identities(rows: list[str]) -> bool:
        identities: list[str] = []
        for row in rows:
            fields = _table_row_fields(row)
            field_names = set(fields)
            if field_names & {
                "action",
                "command",
                "condition",
                "match",
                "order",
                "priority",
                "rank",
                "rule",
                "sequence",
                "step",
            }:
                return False
            if not field_names & {
                "limit",
                "max",
                "maximum",
                "min",
                "minimum",
                "typ",
                "typical",
                "unit",
                "units",
            }:
                return False  # 单独 Value 不能证明行顺序无语义；默认按物理顺序比较。
            identity = _table_row_pairing_primary_identity(
                row,
                allow_generic_column=False,
            )
            if not identity:
                return False
            identities.append(identity)
        return len(identities) == len(set(identities))

    return bool(old_rows or new_rows) and unique_parameter_identities(old_rows) and unique_parameter_identities(new_rows)


def _ordered_table_row_changes(
    old_rows: list[str],
    new_rows: list[str],
) -> list[TableRowChange]:
    """Compare order-sensitive or unknown tables without multiset cancellation."""

    old_keys = [_table_row_display_key(row) for row in old_rows]
    new_keys = [_table_row_display_key(row) for row in new_rows]
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    changes: list[TableRowChange] = []
    for tag, old_start, old_end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_changed = old_rows[old_start:old_end]
        new_changed = new_rows[new_start:new_end]
        paired_count = min(len(old_changed), len(new_changed))
        for index in range(paired_count):
            kind = _table_structured_diff_kind(old_changed[index], new_changed[index])
            if kind != "无变化":
                changes.append(
                    _make_table_row_change(
                        old_changed[index],
                        new_changed[index],
                        kind,
                    )
                )
        for old_row in old_changed[paired_count:]:
            changes.append(_make_table_row_change(old_row, "", "旧表删除行"))
        for new_row in new_changed[paired_count:]:
            changes.append(_make_table_row_change("", new_row, "新表新增行"))
    return changes


def _revision_record_row_changes(
    old_tables: tuple[TableVisual, ...],
    new_tables: tuple[TableVisual, ...],
    old_rows: list[str],
    new_rows: list[str],
) -> list[TableRowChange] | None:
    """Align a proven revision table by its unique ``Revision`` field.

    Revision histories commonly prepend the newest record.  When an extractor
    also changes the date rendering of every existing row, a positional diff
    has no unchanged full-row anchor and shifts each old record onto its
    successor.  The explicit Revision value is the stable record identity, but
    only when both table groups are recognized revision records and every row
    has a unique non-empty value.  Ambiguous or partial tables keep the generic
    order-sensitive fallback.
    """

    if not (
        any(_revision_record_table_key(table) for table in old_tables)
        and any(_revision_record_table_key(table) for table in new_tables)
    ):
        return None
    old_by_revision = _unique_revision_rows(old_rows)
    new_by_revision = _unique_revision_rows(new_rows)
    if old_by_revision is None or new_by_revision is None:
        return None
    changes: list[TableRowChange] = []
    ordered_revisions = list(
        dict.fromkeys([*old_by_revision, *new_by_revision])
    )
    for revision in ordered_revisions:
        old_row = old_by_revision.get(revision, "")
        new_row = new_by_revision.get(revision, "")
        kind = _table_structured_diff_kind(old_row, new_row)
        if kind != "无变化":
            changes.append(_make_table_row_change(old_row, new_row, kind))
    return changes


def _unique_revision_rows(rows: list[str]) -> dict[str, str] | None:
    """Return unique normalized Revision values, or fail closed."""

    by_revision: dict[str, str] = {}
    for row in rows:
        revision = compact_inline(_table_row_fields(row).get("revision", ""))
        if not revision:
            return None
        key = _table_row_text_key(revision, field_label="revision")
        if key in by_revision:
            return None
        by_revision[key] = row
    return by_revision


def _generic_row_equal_under_proven_column_boundary_drift(
    old_row: str,
    new_row: str,
    old_rows: list[str],
    new_rows: list[str],
) -> bool:
    """Ignore a generic row only under one table-wide proven column merge."""

    old_entries = _table_row_field_entries(old_row)
    new_entries = _table_row_field_entries(new_row)
    if not (
        old_entries
        and new_entries
        and len(old_entries) != len(new_entries)
        and all(re.fullmatch(r"column\s+\d+", entry[2], flags=re.I) for entry in old_entries)
        and all(re.fullmatch(r"column\s+\d+", entry[2], flags=re.I) for entry in new_entries)
    ):
        return False
    supported_patterns = _generic_boundary_merge_pattern_evidence(
        old_rows,
        new_rows,
        old_column_count=len(old_entries),
        new_column_count=len(new_entries),
    )
    old_is_header = _generic_boundary_entries_look_like_header(old_entries)
    new_is_header = _generic_boundary_entries_look_like_header(new_entries)
    if old_is_header != new_is_header:
        return False
    return any(
        len(evidence_keys) >= 2
        and (
            _generic_header_matches_boundary_merge_pattern(
                old_entries,
                new_entries,
                pattern,
            )
            if old_is_header
            else _generic_data_matches_boundary_merge_pattern(
                old_entries,
                new_entries,
                pattern,
            )
        )
        for pattern, evidence_keys in supported_patterns.items()
    )  # 两条不同数据行必须证明同一连续列合并；表头 token 也只能在该列组内重排。


def _generic_boundary_merge_pattern_evidence(
    old_rows: list[str],
    new_rows: list[str],
    *,
    old_column_count: int,
    new_column_count: int,
) -> dict[tuple[str, tuple[tuple[int, int], ...]], set[str]]:
    """Return contiguous column-merge patterns proven by distinct body rows."""

    old_body = _generic_boundary_body_entries(old_rows, old_column_count)
    new_body = _generic_boundary_body_entries(new_rows, new_column_count)
    old_by_flattened_key = {
        _generic_boundary_flattened_key(entries): entries
        for entries in old_body
    }
    new_by_flattened_key = {
        _generic_boundary_flattened_key(entries): entries
        for entries in new_body
    }
    evidence: dict[tuple[str, tuple[tuple[int, int], ...]], set[str]] = {}
    for flattened_key in old_by_flattened_key.keys() & new_by_flattened_key.keys():
        for pattern in _generic_boundary_merge_patterns_for_entries(
            old_by_flattened_key[flattened_key],
            new_by_flattened_key[flattened_key],
        ):
            evidence.setdefault(pattern, set()).add(flattened_key)
    return evidence


def _generic_boundary_body_entries(
    rows: list[str],
    column_count: int,
) -> list[list[tuple[int, str, str, str]]]:
    """Collect generic data rows with the same physical width as one header side."""

    collected: list[list[tuple[int, str, str, str]]] = []
    for row in rows:
        entries = _table_row_field_entries(row)
        if (
            len(entries) == column_count
            and all(
                re.fullmatch(r"column\s+\d+", entry[2], flags=re.I)
                for entry in entries
            )
            and not _generic_boundary_entries_look_like_header(entries)
        ):
            collected.append(entries)
    return collected


def _generic_boundary_flattened_key(
    entries: list[tuple[int, str, str, str]],
) -> str:
    """Return a column-boundary-independent key for one generic data row."""

    return _table_row_single_line_text_key(
        compact_inline(" ".join(entry[3] for entry in entries))
    )


def _generic_boundary_merge_patterns_for_entries(
    old_entries: list[tuple[int, str, str, str]],
    new_entries: list[tuple[int, str, str, str]],
) -> set[tuple[str, tuple[tuple[int, int], ...]]]:
    """Find every contiguous larger-side partition matching the smaller side."""

    if len(old_entries) > len(new_entries):
        direction = "old"
        source_entries, target_entries = old_entries, new_entries
    elif len(new_entries) > len(old_entries):
        direction = "new"
        source_entries, target_entries = new_entries, old_entries
    else:
        return set()
    source_count = len(source_entries)
    target_count = len(target_entries)
    target_keys = tuple(
        _table_row_single_line_text_key(entry[3])
        for entry in target_entries
    )
    group_keys = {
        (start, end): _table_row_single_line_text_key(
            compact_inline(
                " ".join(source_entries[index][3] for index in range(start, end))
            )
        )
        for start in range(source_count)
        for end in range(start + 1, source_count + 1)
    }
    pattern_limit = 64
    overflow = False

    @lru_cache(maxsize=None)
    def search(
        source_start: int,
        target_index: int,
    ) -> tuple[tuple[tuple[int, int], ...], ...]:
        nonlocal overflow
        if overflow:
            return ()
        if target_index == target_count:
            return ((),) if source_start == source_count else ()
        remaining_targets = target_count - target_index - 1
        maximum_end = source_count - remaining_targets
        results: list[tuple[tuple[int, int], ...]] = []
        for source_end in range(source_start + 1, maximum_end + 1):
            if group_keys[(source_start, source_end)] != target_keys[target_index]:
                continue
            for tail in search(source_end, target_index + 1):
                results.append(((source_start, source_end), *tail))
                if len(results) > pattern_limit:
                    overflow = True
                    return ()
        return tuple(results)

    groups = search(0, 0)
    if overflow:
        return set()  # 重复/空值造成大量歧义时不选择任一分区，保守报告差异。
    return {(direction, group) for group in groups}


def _generic_header_matches_boundary_merge_pattern(
    old_entries: list[tuple[int, str, str, str]],
    new_entries: list[tuple[int, str, str, str]],
    pattern: tuple[str, tuple[tuple[int, int], ...]],
) -> bool:
    """Verify that header tokens stay inside body-proven merged column groups."""

    direction, groups = pattern
    if direction == "old":
        source_entries, target_entries = old_entries, new_entries
    else:
        source_entries, target_entries = new_entries, old_entries
    return all(
        _generic_header_value_tokens(
            source_entries[index][3]
            for index in range(start, end)
        )
        == _generic_header_value_tokens((target_entries[target_index][3],))
        for target_index, (start, end) in enumerate(groups)
    )


def _generic_data_matches_boundary_merge_pattern(
    old_entries: list[tuple[int, str, str, str]],
    new_entries: list[tuple[int, str, str, str]],
    pattern: tuple[str, tuple[tuple[int, int], ...]],
) -> bool:
    """Verify one data row exactly follows a table-wide contiguous merge pattern."""

    direction, groups = pattern
    if direction == "old":
        source_entries, target_entries = old_entries, new_entries
    else:
        source_entries, target_entries = new_entries, old_entries
    return all(
        _table_row_single_line_text_key(
            compact_inline(
                " ".join(source_entries[index][3] for index in range(start, end))
            )
        )
        == _table_row_single_line_text_key(target_entries[target_index][3])
        for target_index, (start, end) in enumerate(groups)
    )


def _generic_header_value_tokens(values: Iterable[str]) -> tuple[str, ...]:
    """Return ordered tokens for one header cell or one proven cell group."""

    return tuple(
        token.casefold()
        for value in values
        for token in re.findall(r"[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*|[^\W\s]", value)
    )  # Min/Max 或字段词序具有 schema 语义；表体列合并不能授权表头 token 换序。

def _generic_boundary_entries_look_like_header(
    entries: list[tuple[int, str, str, str]],
) -> bool:
    """Recognize technical headers needed only for proven column-boundary drift."""

    if _generic_table_entries_look_like_header(entries):
        return True
    header_words = {"channel", "loss", "mode", "range", "type", "unit", "value"}
    hits = sum(
        bool(set(re.findall(r"[a-z]+", value.casefold())) & header_words)
        for _column, _label, _normalized, value in entries
    )
    return len(entries) >= 3 and hits >= 2

def _table_rows_by_pairing_key(rows: list[str]) -> dict[str, list[str]]:
    """Group rows by stable visible identity while preserving document order."""

    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(_table_row_pairing_key(row), []).append(row)
    return grouped


def _remove_equal_table_rows(
    old_rows: list[str],
    new_rows: list[str],
) -> tuple[list[str], list[str]]:
    """Remove unchanged duplicates before pairing the remaining same-identity rows."""

    new_indexes_by_display_key: dict[str, list[int]] = {}
    for index, new_row in enumerate(new_rows):
        new_indexes_by_display_key.setdefault(
            _table_row_display_key(new_row),
            [],
        ).append(index)
    consumed_count_by_key: dict[str, int] = {}
    matched_new_indexes: set[int] = set()
    unmatched_old: list[str] = []
    for old_row in old_rows:
        old_key = _table_row_display_key(old_row)
        candidate_indexes = new_indexes_by_display_key.get(old_key, [])
        consumed_count = consumed_count_by_key.get(old_key, 0)
        if consumed_count >= len(candidate_indexes):
            unmatched_old.append(old_row)
        else:
            matched_new_indexes.add(candidate_indexes[consumed_count])
            consumed_count_by_key[old_key] = consumed_count + 1
    unmatched_new = [
        row
        for index, row in enumerate(new_rows)
        if index not in matched_new_indexes
    ]
    return unmatched_old, unmatched_new


def _make_table_row_change(old_row: str, new_row: str, kind: str) -> TableRowChange:
    """Convert raw structured rows into one reusable report record."""

    old_note = _table_narrative_row_text(old_row)
    new_note = _table_narrative_row_text(new_row)
    if old_note and new_note:
        return TableRowChange(
            item="表格说明",
            old_value=old_note,
            new_value=new_note,
            change_type=kind,
        )
    hidden_empty_fields = _reader_hidden_empty_table_fields(old_row, new_row)
    if old_note or new_note:
        return TableRowChange(
            item="表格说明/表格行",
            old_value=old_note or _complete_table_row_display(
                old_row,
                hidden_empty_fields=hidden_empty_fields,
            ),
            new_value=new_note or _complete_table_row_display(
                new_row,
                hidden_empty_fields=hidden_empty_fields,
            ),
            change_type=kind,
        )  # 一侧是跨列表格说明、另一侧是普通数据行时，两侧事实都必须完整保留。
    labeled_projection = _table_rows_require_labeled_projection(old_row, new_row)
    value_display = (
        _labeled_table_row_value_display
        if labeled_projection
        else _table_row_value_display
    )
    old_identity = _table_row_identity_text(old_row)
    new_identity = _table_row_identity_text(new_row)
    if (
        old_identity
        and new_identity
        and _table_row_text_key(old_identity) != _table_row_text_key(new_identity)
    ):
        return TableRowChange(
            item="表格项目名称",
            old_value=(
                value_display(old_row, hidden_empty_fields=hidden_empty_fields)
                if labeled_projection
                else _table_row_identity_and_value_display(
                    old_row,
                    old_identity,
                    hidden_empty_fields=hidden_empty_fields,
                )
            ),
            new_value=(
                value_display(new_row, hidden_empty_fields=hidden_empty_fields)
                if labeled_projection
                else _table_row_identity_and_value_display(
                    new_row,
                    new_identity,
                    hidden_empty_fields=hidden_empty_fields,
                )
            ),
            change_type=kind,
        )
    return TableRowChange(
        item=_table_summary_item(old_row, new_row),
        old_value=value_display(
            old_row,
            hidden_empty_fields=hidden_empty_fields,
        ),
        new_value=value_display(
            new_row,
            hidden_empty_fields=hidden_empty_fields,
        ),
        change_type=kind,
    )


def _complete_table_row_display(
    row: str,
    *,
    hidden_empty_fields: set[tuple[int, str]],
) -> str:
    """Return identity plus values for a row paired against a narrative note."""

    identity = _table_row_identity_text(row)
    if identity:
        return _table_row_identity_and_value_display(
            row,
            identity,
            hidden_empty_fields=hidden_empty_fields,
        )
    return _table_row_value_display(
        row,
        hidden_empty_fields=hidden_empty_fields,
    ) or _first_nonempty_table_cell(row)


def _table_row_identity_text(row: str) -> str:
    """Return the reader-visible identity column for a structured row."""

    fields = _table_row_fields(row)
    return _first_table_field(
        fields,
        ("parameter", "characteristic", "description", "label", "name"),
    )


def _table_row_identity_and_value_display(
    row: str,
    identity: str,
    *,
    hidden_empty_fields: set[tuple[int, str]],
) -> str:
    """Show both a changed row name and its otherwise identical data cells."""

    value = _table_row_value_display(
        row,
        hidden_empty_fields=hidden_empty_fields,
    )
    return f"{identity} | {value}" if value else identity


def _reader_hidden_empty_table_fields(
    old_row: str,
    new_row: str,
) -> set[tuple[int, str]]:
    """Hide shared empties while retaining a one-sided empty schema change."""

    old_empty = {
        (column_index, normalized_label)
        for column_index, _label, normalized_label, value in _table_row_field_entries(old_row)
        if not value
    }
    new_empty = {
        (column_index, normalized_label)
        for column_index, _label, normalized_label, value in _table_row_field_entries(new_row)
        if not value
    }
    if old_row and new_row:
        return old_empty & new_empty
    return old_empty | new_empty


def _table_narrative_row_text(row: str) -> str:
    """Return a spanning prose note instead of projecting it as empty data cells."""

    if not row:
        return ""
    entries = _table_row_field_entries(row)
    populated = [entry for entry in entries if entry[3]]
    if len(populated) != 1:
        return ""
    _column, _display_label, normalized_label, value = populated[0]
    if normalized_label not in {
        "parameter",
        "characteristic",
        "description",
        "label",
        "name",
        "notes",
        "note",
    }:
        return ""
    compact = compact_inline(value)
    if len(compact) < 40 or not _READER_PROSE_VERB_RE.search(compact):
        return ""
    return compact_inline(value.replace("↵", " "))


def _table_group_rows(tables: tuple[TableVisual, ...]) -> list[str]:
    """Return all structured rows from a table visual group."""

    rows: list[str] = []  # 保持 PDF 页顺序，跨页表格才能按整组比较。
    for table in tables:
        rows.extend(table.row_texts)
    return rows


def _render_table_row_change(row_change: TableRowChange) -> str:
    """Render one user-readable table-change record."""

    class_name = _table_kind_class(row_change.change_type)
    reader_old_value = _reader_table_inline_text(row_change.old_value)
    reader_new_value = _reader_table_inline_text(row_change.new_value)
    difference_hint = _reader_pair_difference_hint(
        reader_old_value,
        reader_new_value,
    )
    if (
        reader_old_value
        and reader_new_value
        and len(compact_inline(reader_old_value)) <= _READER_TABLE_VALUE_MAX_CHARS
        and len(compact_inline(reader_new_value)) <= _READER_TABLE_VALUE_MAX_CHARS
    ):
        old_value_html, new_value_html = _inline_diff_html(
            reader_old_value,
            reader_new_value,
        )
    else:
        old_value_html = _render_reader_table_cell_html(
            reader_old_value,
            max_chars=_READER_TABLE_VALUE_MAX_CHARS,
            difference_hint=difference_hint,
        )
        new_value_html = _render_reader_table_cell_html(
            reader_new_value,
            max_chars=_READER_TABLE_VALUE_MAX_CHARS,
            difference_hint=difference_hint,
        )
    item_html = _render_reader_table_cell_html(
        _reader_table_inline_text(row_change.item),
        max_chars=_READER_TABLE_ITEM_MAX_CHARS,
    )
    if glyph_note := _unverified_pua_mapping_note(
        row_change.old_value,
        row_change.new_value,
        reader_old_text=reader_old_value,
        reader_new_text=reader_new_value,
        force_reader_equivalent=(
            row_change.change_type == "需人工复核"
            and _table_row_display_key(reader_old_value)
            == _table_row_display_key(reader_new_value)
        ),
    ):
        item_html += f'<div class="change-summary">{_escape(glyph_note)}</div>'
    return (
        "<tr>"
        f"<td>{item_html}</td>"
        f"<td>{old_value_html}</td>"
        f"<td>{new_value_html}</td>"
        f'<td><span class="table-kind {class_name}">{_escape(row_change.change_type)}</span></td>'
        "</tr>"
    )


def _reader_table_inline_text(value: str) -> str:
    """Decode reader glyphs and normalize only explicitly labelled symbols."""

    decoded = compact_inline(
        readable_symbol_font_glyphs(
            _normalize_generic_table_header_wraps(value)
        ).replace("↵", " ")
    )
    parts: list[str] = []
    for part in decoded.split(" | "):
        symbol_field = re.fullmatch(
            r"(?P<label>Symbol(?:\[\d+\])?)=(?P<value>.*)",
            part,
            flags=re.I,
        )
        if symbol_field is None:
            parts.append(part)
            continue
        symbol_value = _normalize_compact_symbol_spacing(
            symbol_field.group("value"),
            field_label="Symbol",
        )
        parts.append(f'{symbol_field.group("label")}={symbol_value}')
    return " | ".join(parts)


def _reader_table_cell_text(
    value: str,
    *,
    max_chars: int,
    difference_hint: str = "",
) -> str:
    """Return a bounded reader summary while audit formats keep the full cell."""

    compact = compact_inline(value)
    if len(compact) <= max_chars:
        return compact
    preview = _reader_snippet_preview(compact, max_chars=min(96, max_chars // 2))
    hint = f"；{difference_hint}" if difference_hint else ""
    return (
        f"超长单元格已折叠（{len(compact)} 字）；开头“{preview}”"
        f"{hint}；完整值见 JSON/CSV 或展开 HTML。"
    )


def _render_reader_table_cell_html(
    value: str,
    *,
    max_chars: int,
    difference_hint: str = "",
) -> str:
    """Render a long table cell closed by default without discarding evidence."""

    compact = compact_inline(value)
    summary = _reader_table_cell_text(
        compact,
        max_chars=max_chars,
        difference_hint=difference_hint,
    )
    if len(compact) <= max_chars:
        return _escape(summary)
    return (
        '<details class="table-cell-detail">'
        f"<summary>{_escape(summary)}</summary>"
        f'<div class="table-cell-detail-body">{_escape(value)}</div>'
        "</details>"
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


def _table_row_value_display(
    row: str,
    *,
    hidden_empty_fields: set[tuple[int, str]] | None = None,
) -> str:
    """Return a compact value summary without dropping changed conditions."""

    if not row:
        return ""
    hidden_empty_fields = hidden_empty_fields or set()
    entries = _table_row_field_entries(row)
    label_counts: dict[str, int] = {}
    for _column_index, _display_label, normalized_label, _value in entries:
        label_counts[normalized_label] = label_counts.get(normalized_label, 0) + 1
    if any(count > 1 for count in label_counts.values()):
        return _labeled_table_row_value_display(
            row,
            hidden_empty_fields=hidden_empty_fields,
        )
    fields = _table_row_fields(row)
    if not fields:
        return " | ".join(
            _table_cell_value_display(decode_table_cell(cell))
            for cell in _table_row_cells_for_display(row)
        )
    parts: list[str] = []  # 用户更关心符号和值/单位，描述字段已放到项目列。
    symbol = fields.get("symbol")
    if symbol:
        parts.append(f"Symbol={symbol}")
    value_parts = [
        value
        for key in ("value", "values", "min", "minimum", "typ", "typical", "max", "maximum")
        if (value := fields.get(key))
    ]  # 数值列保留多个上下限，避免只显示 Symbol。
    unit = _first_table_field(fields, ("unit", "units"))
    if value_parts:
        value_text = " / ".join(value_parts)
        parts.append(f"{value_text} {unit}".strip() if unit else value_text)
    elif unit:
        unit_label = next(
            (
                display_label
                for _column_index, display_label, normalized_label, value in entries
                if normalized_label in {"unit", "units"} and value == unit
            ),
            "Unit",
        )
        parts.append(f"{unit_label}={unit}")
    parts.extend(_additional_table_field_displays(row))  # Condition/Notes/列N 等证据也必须进入 HTML、JSON 和 CSV。
    parts.extend(
        f"{display_label}={_READER_EMPTY_VALUE}"
        for _column_index, display_label, _normalized_label, value in entries
        if not value
        and (_column_index, _normalized_label) not in hidden_empty_fields
    )  # 空字段也是 schema/occurrence 证据，不能只在内部 key 中变化而报告仍显示相同值。
    return " | ".join(part for part in parts if part)


def _table_rows_require_labeled_projection(old_row: str, new_row: str) -> bool:
    """Use a lossless labelled projection when compact fields would be ambiguous."""

    old_entries = _table_row_field_entries(old_row)
    new_entries = _table_row_field_entries(new_row)
    old_labels = [entry[2] for entry in old_entries]
    new_labels = [entry[2] for entry in new_entries]
    if old_row and new_row and old_labels != new_labels:
        return True  # Min->Minimum / Value->Max 是 schema 变化，数值相同也必须显示字段名。
    for row in (old_row, new_row):
        cells = _table_row_cells_for_display(row)
        parsed_flags = [split_table_field(cell) is not None for cell in cells]
        if any(parsed_flags) and not all(parsed_flags):
            return True  # Header=Value 与无标签单元格混用时，紧凑投影会丢失位置内容。
    identity_labels = {"parameter", "characteristic", "description", "label", "name"}
    unit_labels = {"unit", "units"}
    for entries in (old_entries, new_entries):
        if sum(entry[2] in identity_labels for entry in entries) > 1:
            return True  # item 列只容纳一个主身份，其余 Description/Name 不能被投影丢弃。
        if sum(entry[2] in unit_labels for entry in entries) > 1:
            return True  # Unit 和 Units 同时存在时不能只显示第一个。
    return False


def _labeled_table_row_value_display(
    row: str,
    *,
    hidden_empty_fields: set[tuple[int, str]] | None = None,
) -> str:
    """Project every observed field with its label for lossless reader evidence."""

    if not row:
        return ""
    hidden_empty_fields = hidden_empty_fields or set()
    cells = _table_row_cells_for_display(row)
    entries = _table_row_field_entries(row)
    label_counts = Counter(entry[2] for entry in entries)
    occurrences: Counter[str] = Counter()
    parts: list[str] = []
    for column_index, cell in enumerate(cells):
        parsed_field = split_table_field(cell)
        if parsed_field is None:
            value = _table_cell_value_display(decode_table_cell(cell))
            parts.append(f"列{column_index + 1}={value or _READER_EMPTY_VALUE}")
            continue
        display_label, raw_value = parsed_field
        display_label = compact_inline(display_label)
        normalized_label = display_label.casefold()
        value = _table_cell_value_display(raw_value)
        occurrences[normalized_label] += 1
        if not value and (column_index, normalized_label) in hidden_empty_fields:
            continue
        suffix = (
            f"[{occurrences[normalized_label]}]"
            if label_counts[normalized_label] > 1
            else ""
        )
        parts.append(f"{display_label}{suffix}={value or _READER_EMPTY_VALUE}")
    return " | ".join(parts)


def _table_structured_diff_kind(old_row: str, new_row: str) -> str:
    """Classify one table summary row with a short review label."""

    if old_row and new_row and _table_row_display_key(old_row) == _table_row_display_key(new_row):
        return "无变化"
    if old_row and new_row and _table_rows_equal_across_observed_schema(old_row, new_row):
        return "无变化"
    if _table_rows_differ_only_by_unproven_reader_glyph_mapping(old_row, new_row):
        return "需人工复核"
    if _table_rows_differ_only_by_one_sided_blank_unit(old_row, new_row):
        return "需人工复核"
    if _table_rows_differ_only_by_numeric_internal_spacing(old_row, new_row):
        return "需人工复核"
    if old_row and not new_row:
        return "旧表删除行"
    if new_row and not old_row:
        return "新表新增行"
    if _table_symbol_changed(old_row, new_row):
        return "实质/符号变化"
    if _table_numeric_value_changed(old_row, new_row):
        return "实质变化"
    return "替换/修改"


def _table_rows_differ_only_by_numeric_internal_spacing(
    old_row: str,
    new_row: str,
) -> bool:
    """Flag digit spacing created by text extraction without claiming equality."""

    if not old_row or not new_row:
        return False
    old_entries = _table_row_field_entries(old_row)
    new_entries = _table_row_field_entries(new_row)
    if len(old_entries) != len(new_entries) or not old_entries:
        return False
    saw_spacing_difference = False
    for old_entry, new_entry in zip(old_entries, new_entries, strict=True):
        if old_entry[:3] != new_entry[:3]:
            return False
        old_value = compact_inline(old_entry[3])
        new_value = compact_inline(new_entry[3])
        field_label = old_entry[2]
        if _table_row_text_key(old_value, field_label=field_label) == (
            _table_row_text_key(new_value, field_label=field_label)
        ):
            continue
        old_compact_digits = re.sub(r"\s+", "", old_value)
        new_compact_digits = re.sub(r"\s+", "", new_value)
        if not (
            old_compact_digits == new_compact_digits
            and re.fullmatch(r"[+\-−]?\d+(?:\.\d+)?", old_compact_digits)
            and (
                re.search(r"(?<=\d)\s+(?=\d)", old_value)
                or re.search(r"(?<=\d)\s+(?=\d)", new_value)
            )
        ):
            return False
        saw_spacing_difference = True
    return saw_spacing_difference


def _table_rows_differ_only_by_one_sided_blank_unit(
    old_row: str,
    new_row: str,
) -> bool:
    """Downgrade an otherwise identical blank/unit pair to explicit review.

    A trailing unit is a common last-glyph extraction loss in dense table
    cells.  The report must not claim a confirmed semantic change when every
    independently observed field agrees and the sole discrepancy is one blank
    Unit/Units cell.  The row remains visible as review evidence rather than
    being silently normalized away.
    """

    if not old_row or not new_row:
        return False
    old_entries = _table_row_field_entries(old_row)
    new_entries = _table_row_field_entries(new_row)
    if len(old_entries) != len(new_entries) or not old_entries:
        return False
    differences: list[
        tuple[tuple[int, str, str, str], tuple[int, str, str, str]]
    ] = []
    for old_entry, new_entry in zip(old_entries, new_entries, strict=True):
        if old_entry[:3] != new_entry[:3]:
            return False
        field_label = old_entry[2]
        if _table_row_text_key(old_entry[3], field_label=field_label) != (
            _table_row_text_key(new_entry[3], field_label=field_label)
        ):
            differences.append((old_entry, new_entry))
    if len(differences) != 1:
        return False
    old_entry, new_entry = differences[0]
    return (
        old_entry[2] in {"unit", "units"}
        and bool(old_entry[3]) != bool(new_entry[3])
        and bool(compact_inline(old_entry[3] or new_entry[3]))
    )


def _table_rows_differ_only_by_unproven_reader_glyph_mapping(
    old_row: str,
    new_row: str,
) -> bool:
    """Flag PUA/Unicode lookalikes for review instead of silently equating them."""

    if not old_row or not new_row:
        return False
    if not re.search(r"[\ue000-\uf8ff]", old_row + new_row):
        return False
    return _table_row_display_key(readable_symbol_font_glyphs(old_row)) == (
        _table_row_display_key(readable_symbol_font_glyphs(new_row))
    )


def _table_rows_equal_across_observed_schema(old_row: str, new_row: str) -> bool:
    """Compare generic ``Column N`` rows with an explicitly headed counterpart.

    Some PDF revisions expose the same cells once under neutral columns and
    once under Parameter/Symbol/Value/Unit headers.  Equality is accepted only
    when every cell is present in the same order and matches under the explicit
    field's semantics; extra notes, changed values, or reordered columns remain
    visible.
    """

    old_entries = _table_row_field_entries(old_row)
    new_entries = _table_row_field_entries(new_row)
    old_generic = bool(old_entries) and all(
        re.fullmatch(r"column\s+\d+", label, flags=re.I)
        for _column, label, _normalized, _value in old_entries
    )
    new_generic = bool(new_entries) and all(
        re.fullmatch(r"column\s+\d+", label, flags=re.I)
        for _column, label, _normalized, _value in new_entries
    )
    if old_generic and new_generic:
        if len(old_entries) != len(new_entries):
            return False  # 不同列数只能由带表体证据的 boundary-merge 路径授权；单行全局展平会吞 schema 变化。
        old_is_header = _generic_table_entries_look_like_header(old_entries)
        new_is_header = _generic_table_entries_look_like_header(new_entries)
        return all(
            _table_row_text_key(
                _normalize_generic_table_header_wraps(old_entry[3])
                if old_is_header and new_is_header
                else old_entry[3]
            )
            == _table_row_text_key(
                _normalize_generic_table_header_wraps(new_entry[3])
                if old_is_header and new_is_header
                else new_entry[3]
            )
            for old_entry, new_entry in zip(old_entries, new_entries, strict=True)
        )  # 同列数时逐列比较；可忽略单元格内软换行，但不跨字段搬运 token。
    if old_generic == new_generic:
        return False
    generic_entries, explicit_entries = (
        (old_entries, new_entries) if old_generic else (new_entries, old_entries)
    )
    if len(generic_entries) != len(explicit_entries):
        return False
    for generic, explicit in zip(generic_entries, explicit_entries):
        generic_value = generic[3]
        explicit_label = explicit[2]
        explicit_value = explicit[3]
        if _cross_schema_table_value_key(generic_value, explicit_label) != (
            _cross_schema_table_value_key(explicit_value, explicit_label)
        ):
            return False
    return True


def _generic_table_entries_look_like_header(
    entries: list[tuple[int, str, str, str]],
) -> bool:
    """Require header vocabulary in multiple cells before soft-wrap repair."""

    header_words = {
        "characteristic",
        "condition",
        "description",
        "frequency",
        "jitter",
        "maximum",
        "minimum",
        "parameter",
        "range",
        "symbol",
        "typical",
        "unit",
        "units",
        "value",
    }
    hits = 0
    for _column, _label, _normalized, value in entries:
        words = set(re.findall(r"[a-z]+", value.casefold()))
        if words & header_words:
            hits += 1
    return len(entries) >= 2 and hits >= 2


def _normalize_generic_table_header_wraps(value: str) -> str:
    """Remove only codec-proven header line breaks, never literal slash glyphs."""

    normalized = re.sub(
        r"(?<=[,;:])\s*↵\s*(?=[A-Za-z])",
        " ",
        value,
    )
    normalized = re.sub(
        r"(?<=-)\s*↵\s*(?=[A-Za-z])",
        "",
        normalized,
    )
    normalized = re.sub(
        r"\s*↵\s*(?=\([^()]+\)\s*$)",
        " ",
        normalized,
    )
    return compact_inline(normalized)


def _cross_schema_table_value_key(value: str, field_label: str) -> str:
    """Normalize one cell under the explicit schema observed on the other side."""

    return _table_row_text_key(value, field_label=field_label)


def _table_kind_class(kind: str) -> str:
    """Return a CSS class for a structured table summary kind."""

    if kind == "无变化":
        return "table-kind-same"
    if "删除" in kind:
        return "table-kind-del"
    if "新增" in kind:
        return "table-kind-add"
    if "复核" in kind:
        return "table-kind-review"
    return "table-kind-change"


def _table_row_fields(row: str) -> dict[str, str]:
    """Parse ``Header=Value`` cells from one structured table row."""

    fields: dict[str, str] = {}  # 小写字段名映射到原始显示值。
    for cell in _table_row_cells_for_display(row):
        parsed_field = split_table_field(cell)
        if parsed_field is None:
            continue
        key, value = parsed_field
        normalized_key = compact_inline(key).casefold()
        normalized_value = _table_cell_value_display(value)
        if normalized_key and normalized_value:
            fields[normalized_key] = normalized_value
    return fields


def _table_row_field_entries(row: str) -> list[tuple[int, str, str, str]]:
    """Return every decoded field in column order, including duplicate labels."""

    entries: list[tuple[int, str, str, str]] = []
    for column_index, cell in enumerate(_table_row_cells_for_display(row)):
        parsed_field = split_table_field(cell)
        if parsed_field is None:
            continue
        label, value = parsed_field
        display_label = compact_inline(label)
        normalized_label = display_label.casefold()
        if normalized_label:
            entries.append(
                (
                    column_index,
                    display_label,
                    normalized_label,
                    _table_cell_value_display(value),
                )
            )
    return entries


def _table_row_cells_for_display(row: str) -> list[str]:
    """Split a structured table row into display cells without the T-number prefix."""

    text = " ".join(row.splitlines()).strip()
    if text.startswith("表格行:"):
        text = text[len("表格行:") :].strip()
    cells = split_table_cells(text)
    if cells and re.fullmatch(r"T\d+", decode_table_cell(cells[0]), flags=re.I):
        return cells[1:]
    return cells


def _table_cell_value_display(value: str) -> str:
    """Render decoded cell line boundaries without confusing them with literal ``/``."""

    return " ↵ ".join(
        compact_inline(line)
        for line in value.split("\n")
    )  # 保留原始字符供 TableRowChange/JSON/CSV 审计；HTML/MD/TXT 在最终渲染边界转为可读 glyph。


def _first_table_field(fields: dict[str, str], names: tuple[str, ...]) -> str:
    """Return the first populated field from a list of normalized names."""

    return next((fields[name] for name in names if fields.get(name)), "")


def _first_nonempty_table_cell(row: str) -> str:
    """Return the first useful cell from a structured table row."""

    for cell in _table_row_cells_for_display(row):
        parsed_field = split_table_field(cell)
        if parsed_field is not None:
            _key, value = parsed_field
            if value.strip():
                return _table_cell_value_display(value)
        elif decode_table_cell(cell):
            return _table_cell_value_display(decode_table_cell(cell))
    return ""


def _table_symbol_changed(old_row: str, new_row: str) -> bool:
    """Return True when the symbol column changed for the same table row."""

    if not old_row or not new_row:
        return False
    old_symbol = _table_row_fields(old_row).get("symbol", "")
    new_symbol = _table_row_fields(new_row).get("symbol", "")
    return bool(
        old_symbol
        and new_symbol
        and _table_row_text_key(old_symbol, field_label="symbol")
        != _table_row_text_key(new_symbol, field_label="symbol")
    )


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


def _additional_table_field_displays(row: str) -> list[str]:
    """Return non-identity fields not already represented by symbol/value/unit."""

    skipped = {
        "parameter",
        "characteristic",
        "description",
        "label",
        "name",
        "symbol",
        "value",
        "values",
        "min",
        "minimum",
        "typ",
        "typical",
        "max",
        "maximum",
        "unit",
        "units",
    }
    displays: list[str] = []
    for cell in _table_row_cells_for_display(row):
        parsed_field = split_table_field(cell)
        if parsed_field is None:
            continue
        key, value = parsed_field
        normalized_key = compact_inline(key).casefold()
        if normalized_key in skipped or not compact_inline(value):
            continue
        displays.append(f"{compact_inline(key)}={_table_cell_value_display(value)}")
    return displays


def _table_row_pairing_key(row: str) -> str:
    """Build a row identity key that ignores value changes for alignment."""

    fields = _table_row_fields(row)  # Header=Value 行优先按参数名/特性名配对，避免插入行造成错位。
    if primary_identity := _table_row_pairing_primary_identity(row):
        return f"label:{primary_identity}"
    symbol = fields.get("symbol", "")
    if symbol:
        return f"symbol:{_table_row_display_key(symbol)}"  # 缺少描述列时才用 symbol 作为退路身份。
    cells = _table_row_cells_for_display(row)
    if cells:
        return f"cell:{_table_row_display_key(cells[0])}"  # 无字段名的旧表格用第一列做人工可见身份。
    return _table_row_display_key(row)


def _table_row_pairing_primary_identity(
    row: str,
    *,
    allow_generic_column: bool = True,
) -> str:
    """Return a reader-mapped alignment key while keeping equality raw-aware."""

    fields = _table_row_fields(row)
    field_label = ""
    label = _first_table_field(
        fields,
        ("parameter", "characteristic", "description", "label", "name"),
    )
    if not label:
        label = fields.get("symbol", "")
        if label:
            field_label = "symbol"
    if not label and allow_generic_column:
        label = fields.get("column 1", "")
    if compact_inline(label).casefold() in {
        "parameter",
        "characteristic",
        "description",
        "label",
        "name",
        "symbol",
    }:
        return ""
    if not label:
        return ""
    return _table_row_text_key(
        readable_symbol_font_glyphs(label),
        field_label=field_label,
    )  # 映射只用于把潜在同一行送入“需复核”分类；显示 key 和 JSON 仍保留原码位。


def _table_row_primary_identity(row: str) -> str:
    """Return an explicit identity or the same first cell under generic Column N schema."""

    fields = _table_row_fields(row)
    identity_field_label = ""
    label = _first_table_field(
        fields,
        ("parameter", "characteristic", "description", "label", "name"),
    )
    if not label:
        label = fields.get("symbol", "")
        if label:
            identity_field_label = "symbol"
    if not label:
        label = fields.get("column 1", "")
    if compact_inline(label).casefold() in {
        "parameter",
        "characteristic",
        "description",
        "label",
        "name",
        "symbol",
    }:
        return ""  # 续页重复表头不是数据行身份。
    if not label:
        return ""
    if identity_field_label:
        return _table_row_text_key(label, field_label=identity_field_label)
    return _table_row_display_key(label)


def _table_groups_mix_generic_and_explicit_schema(
    old_rows: list[str],
    new_rows: list[str],
) -> bool:
    """Return whether exactly one side contains neutral Column N row labels."""

    def has_generic_rows(rows: list[str]) -> bool:
        return any(
            bool(entries)
            and all(
                re.fullmatch(r"column\s+\d+", label, flags=re.I)
                for _column, label, _normalized, _value in entries
            )
            for row in rows
            if (entries := _table_row_field_entries(row))
        )

    return has_generic_rows(old_rows) != has_generic_rows(new_rows)


def _table_row_display_key(row: str) -> str:
    """Normalize one visual table row for row-level summary matching."""

    cells = _table_row_cells_for_display(row)  # 先去掉内部 T1/T2 行号，避免行号变化制造伪差异。
    row_context_parts: list[str] = []
    for cell in cells:
        parsed_field = split_table_field(cell)
        if parsed_field is None:
            continue
        context_key, context_value = parsed_field
        normalized_context_key = compact_inline(context_key).casefold()
        row_context_parts.append(compact_inline(context_key))
        if normalized_context_key in {
            "parameter",
            "characteristic",
            "description",
            "label",
            "name",
        }:
            row_context_parts.append(_table_cell_value_display(context_value))
    row_context = " ".join(row_context_parts)
    structured_cells: list[tuple[int, str, str]] = []
    unstructured_cells: list[tuple[int, str]] = []
    for column_index, cell in enumerate(cells):
        parsed_field = split_table_field(cell)
        if parsed_field is None:
            decoded_cell = decode_table_cell(cell)
            unstructured_cells.append(
                (column_index, _table_row_text_key(decoded_cell))
            )
            continue
        key, value = parsed_field
        normalized_key = _case_aware_display_text_key(key)
        normalized_value = _table_row_text_key(
            value,
            field_label=compact_inline(f"{key} {row_context}").casefold(),
        )
        if normalized_key:
            structured_cells.append((column_index, normalized_key, normalized_value))
        else:
            unstructured_cells.append(
                (column_index, _table_row_text_key(decode_table_cell(cell)))
            )
    if structured_cells:
        key_counts: dict[str, int] = {}
        for _column_index, key, _value in structured_cells:
            key_counts[key] = key_counts.get(key, 0) + 1
        unique_fields = sorted(
            f"{key}={value}"
            for _column_index, key, value in structured_cells
            if key_counts[key] == 1
        )
        duplicate_fields = [
            f"column:{column_index}:{key}={value}"
            for column_index, key, value in structured_cells
            if key_counts[key] > 1
        ]  # 重复名字段必须绑定列位置，不能把 Value=1/Value=2 换序后排回相同。
        positional_cells = [
            f"column:{column_index}:{value or '<empty>'}"
            for column_index, value in unstructured_cells
        ]
        return " | ".join([*unique_fields, *duplicate_fields, *positional_cells])
    return " | ".join(
        f"column:{column_index}:{value or '<empty>'}"
        for column_index, value in unstructured_cells
    )


def _table_row_text_key(value: str, *, field_label: str = "") -> str:
    """Normalize table text while preserving every observable token."""

    line_keys = [
        _table_row_single_line_text_key(line, field_label=field_label)
        for line in value.split("\n")
    ]
    return json.dumps(line_keys, ensure_ascii=False, separators=(",", ":"))
    # 所有单行/多行值都编码为 JSON string tuple；长度和转义有边界，任意原文都不能伪造换行结构。


def _table_row_single_line_text_key(value: str, *, field_label: str = "") -> str:
    """Normalize one physical line of a decoded table cell."""

    compact_source = _normalize_unambiguous_leading_list_marker(
        compact_inline(value)
    )  # 无字体来源的 PUA 保留在比较 key；只在读者渲染边界做可读替换。
    compact_source = _normalize_compact_symbol_spacing(
        compact_source,
        field_label=field_label,
    )
    case_source = compact_source
    case_source = case_source.replace("µ", "u").replace("μ", "u")
    case_source = _normalize_table_row_math_text(case_source)
    token_key = " ".join(
        token.key
        for token in _inline_tokens(case_source, field_label=field_label)
    )
    components = [token_key]
    signature_source = re.sub(
        r"(?<=\d)\s*[xX]\s*(?=\d)",
        "×",
        compact_source,
    )  # 数字间 x 已有明确乘法语境，不应再制造标识符边界差异。
    components.extend(
        f"identifier-boundary:{signature}"
        for signature in identifier_boundary_signatures(
            signature_source,
            context=field_label,
        )
    )
    components.extend(
        f"micro-identifier:{signature}"
        for signature in micro_identifier_signatures(
            signature_source,
            context=field_label,
        )
    )
    if field_label:
        components.append(
            "structured-shape:"
            + json.dumps(
                _structured_value_shape(case_source),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        components.extend(
            f"structured-case:{signature}"
            for signature in _technical_case_signatures(case_source)
        )
    if re.search(r"(?i)\b(?:regex|regexp|pattern)\b", field_label):
        components.append(
            "structured-pattern:"
            + json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )
    return " | ".join(components)


def _normalize_compact_symbol_spacing(value: str, *, field_label: str) -> str:
    """Remove one extractor gap inside a compact engineering symbol only."""

    if not re.match(r"(?i)^symbol(?:\b|$)", compact_inline(field_label)):
        return value
    numeric_match = re.fullmatch(
        r"([A-Za-zͰ-Ͽ-][A-Za-z0-9_]{0,7})\s+(\d+[A-Za-z0-9_.+-]*)",
        value,
    )
    if numeric_match is not None and (
        len(numeric_match.group(1)) == 1
        or numeric_match.group(1) in {"JH", "EOJ", "JRMS", "T_JH"}
    ):
        return f"{numeric_match.group(1)}{numeric_match.group(2)}"
    letter_match = re.fullmatch(
        r"([A-Za-z])\s+([a-z]+)([0-9_.+-]{0,2})",
        value,
    )
    if (
        letter_match is None
        or not is_known_engineering_symbol_letter_suffix(
            letter_match.group(1),
            letter_match.group(2),
        )
    ):
        return value
    return "".join(letter_match.groups())


def _structured_value_shape(value: str) -> str:
    """Encode visible boundary punctuation while ignoring spacing inside word runs."""

    for source, target in (
        ("<=>", "⇔"),
        ("<->", "↔"),
        ("->", "→"),
        ("<-", "←"),
        ("=>", "⇒"),
        ("<=", "≤"),
        (">=", "≥"),
        ("!=", "≠"),
    ):
        value = value.replace(source, target)
    shape: list[str] = []
    inside_word_run = False
    for character in value:
        if character.isalnum() or character == "_":
            if not inside_word_run:
                shape.append("w")
            inside_word_run = True
            continue
        if character.isspace():
            continue
        shape.append(character)
        inside_word_run = False
    return "".join(shape)


def _technical_case_signatures(
    value: str,
    *,
    target_span: tuple[int, int] | None = None,
) -> list[str]:
    """Preserve case only where token shape indicates technical semantics."""

    signatures: list[str] = []
    for match in _TABLE_CASE_BEARING_TOKEN_RE.finditer(value):
        if target_span is not None and match.span() != target_span:
            continue
        token = match.group(0)
        letters = [character for character in token if character.isalpha()]
        cased_letters = [
            character
            for character in letters
            if character.lower() != character.upper()
        ]
        has_letter_and_digit = bool(cased_letters) and any(character.isdigit() for character in token)
        has_internal_upper = any(
            character.isupper()
            for character in token[1:]
            if character.lower() != character.upper()
        )
        is_all_upper = len(cased_letters) >= 2 and all(
            character.isupper() for character in cased_letters
        )
        is_single_letter = len(token) == 1 and len(cased_letters) == 1
        is_titlecase = bool(cased_letters) and cased_letters[0].isupper() and all(
            character.islower() for character in cased_letters[1:]
        )
        has_non_ascii_case = any(
            ord(character) > 127 and character.lower() != character.upper()
            for character in cased_letters
        )
        if (
            "_" in token
            or has_letter_and_digit
            or has_internal_upper
            or is_all_upper
            or (is_single_letter and _single_letter_case_is_technical(value, match.start(), match.end()))
            or (
                is_titlecase
                and _titlecase_token_is_technical(
                    value,
                    match.start(),
                    match.end(),
                    token,
                    cased_letters,
                )
            )
            or (has_non_ascii_case and any(character.isupper() for character in cased_letters))
            or (
                _span_is_inside_paired_literal(value, match.start(), match.end())
                and any(character.isupper() for character in cased_letters)
            )
        ):
            signatures.append(token)
    return signatures


def _single_letter_case_is_technical(value: str, start: int, end: int) -> bool:
    """Use visible local context to distinguish variables/units from prose articles."""

    if re.fullmatch(r"\s*[^\W\d_]\s*", value):
        return True
    before = value[:start].rstrip()
    after = value[end:].lstrip()
    if re.search(r"\d\s*$", before):
        return True
    if re.search(
        r"(?i)\b(?:variable|state|mode|symbol|unit|enum|class|grade|level|channel|port|pin|node)\s*$",
        before,
    ):
        return True
    if re.search(r"(?i)\b(?:route|drive)\b", value):
        return True  # 路由/驱动表达式中的单字母通常是端点、变量或单位。
    if before.endswith(
        (
            "=", "(", "[", "{", "+", "-", "*", "/", "×", "÷", "!", "<", ">",
            "→", "←", "↔", "⇒", "⇐", "⇔", "≤", "≥", "≠", "∈", "∉", "∧", "∨",
        )
    ):
        return True
    if after.startswith(
        (
            "=", ")", "]", "}", "+", "-", "*", "/", "×", "÷", "<", ">", "!",
            "→", "←", "↔", "⇒", "⇐", "⇔", "≤", "≥", "≠", "∈", "∉", "∧", "∨",
        )
    ):
        return True
    return False


def _titlecase_token_is_technical(
    value: str,
    start: int,
    end: int,
    token: str,
    cased_letters: list[str],
) -> bool:
    """Recognize short units/endpoints without treating prose Titlecase as semantic."""

    before = value[:start].rstrip()
    after = value[end:].lstrip()
    if _span_has_composite_identifier_context(value, start, end):
        return True
    if _identifier_span_has_expression_context(value, start, end):
        return True
    if re.search(r"(?:[^\W\d_]|_)\w*\s*:\s*$", before) and not re.search(r"\w", after):
        return True  # `Class: Foo` 这类短 label/value 对是显式结构，不是普通句首大写。
    if re.search(r"=\s*$", before) or re.match(r"^(?:=|\()", after):
        return True  # 赋值两侧和函数调用名是显式技术上下文。
    if re.search(r"\d\s*$", before):
        return True  # 数值后的 Mb/s、Hz、Pa、Ah 等形态依赖大小写。
    if len(cased_letters) == 2 and token[-1:].casefold() == "x":
        return True  # Rx/Tx 这类两字母端点缩写有稳定的形态特征。
    if compact_inline(value) == token and len(cased_letters) <= 3:
        return True  # 独立短值更可能是单位/枚举，而非普通句首词。
    if re.search(
        r"(?i)\b(?:enum|unit|symbol|mode|state|value|option|port|pin|channel|endpoint)\s*$",
        before,
    ):
        return True
    return bool(re.match(r"(?i)^(?:path|lane|port|pin|channel|state|mode)\b", after))


def _span_has_composite_identifier_context(value: str, start: int, end: int) -> bool:
    """Recognize a token joined to URI, path, generic, or namespace syntax."""

    before = value[:start]
    after = value[end:]
    return bool(
        re.search(r"(?:[:./\\<>@]|::)$", before)
        or re.match(r"^(?:[:./\\<>@]|::)", after)
    )


def _identifier_span_has_expression_context(value: str, start: int, end: int) -> bool:
    """Propagate case evidence through an assignment, call, or list expression."""

    line_start = value.rfind("\n", 0, start) + 1
    if "=" in value[line_start:start]:
        return True
    for opening, closing in (("[", "]"), ("{", "}")):
        opening_index = _enclosing_delimiter_index(value, start, opening, closing)
        if opening_index >= 0 and value.find(closing, end) >= 0:
            return True
    opening_index = _enclosing_delimiter_index(value, start, "(", ")")
    if opening_index < 0 or value.find(")", end) < 0:
        return False
    return bool(
        re.search(
            r"(?:[^\W\d_]|_)\w*\s*$",
            value[:opening_index],
        )
    )  # 只有 identifier(...) 证明是函数调用，普通括号插入语不扩大大小写语义。


def _enclosing_delimiter_index(
    value: str,
    start: int,
    opening: str,
    closing: str,
) -> int:
    """Return the nearest unmatched opening delimiter before a token."""

    depth = 0
    for index in range(start - 1, -1, -1):
        if value[index] == closing:
            depth += 1
        elif value[index] == opening:
            if depth == 0:
                return index
            depth -= 1
    return -1


def _case_aware_display_text_key(value: str) -> str:
    """Ignore prose capitalization while retaining technical-token case."""

    compact = compact_inline(value)
    signatures = _technical_case_signatures(compact)
    return " | ".join(
        [compact.casefold(), *(f"case:{token}" for token in signatures)]
    )


def _normalize_unambiguous_leading_list_marker(value: str) -> str:
    """Normalize only explicit Unicode bullets and numeric list delimiters."""

    normalized = re.sub(r"^\s*[•●⚫]\s+", "", value)
    return re.sub(r"^\s*(\d{1,3})[.)]\s+", r"\1 ", normalized)


def _normalize_table_row_math_text(value: str) -> str:
    """Normalize table-row math notation for visual summary matching."""

    hexadecimal_literals: list[str] = []

    def protect_hexadecimal(match: re.Match[str]) -> str:
        hexadecimal_literals.append(match.group(0))
        return f"\ue000{len(hexadecimal_literals) - 1}\ue001"

    normalized = _HEX_LITERAL_RE.sub(protect_hexadecimal, value)
    normalized = normalized.replace("−", "-").replace("–", "-").replace("—", " - ")  # 数学负号和破折号统一。
    normalized = re.sub(r"(?i)\b(note|test|section|table|figure)\s*(\d)", r"\1 \2", normalized)  # Note2/Note 2 等价。
    normalized = re.sub(
        r"(?<![A-Za-z])([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:x|×|\*)\s*10\s*"
        r"\^\s*([+-]?\d+)",
        lambda match: f"{match.group(1)}e{match.group(2)}",
        normalized,
    )  # 只有 ^ 或显式正负号才证明 10 后是指数；2x100 仍是普通乘法。
    normalized = re.sub(
        r"(?<=\d)\s*(?:x|×|\*)\s*(?=\d)",
        " × ",
        normalized,
    )  # 数字与数字间的 x/×/* 是可见乘号，不得删成空格。
    normalized = re.sub(
        r"(?<=\d)\s*(?:x|×|\*)\s*(?=[^\W\d_]|_)",
        " × ",
        normalized,
    )  # 2xT_Vf / 2×T_Vf / 2*T_Vf 都保留一个明确乘号。
    normalized = re.sub(
        r"(?<=[^\W\d_])\s*(?:×|\*)\s*(?=[^\W_])",
        " × ",
        normalized,
    )  # fb*n / fb×n 以及 A*B / A×B 统一为显式乘法。
    normalized = re.sub(
        r"(?<=\w)\s+[x]\s+(?=\w)",
        " × ",
        normalized,
    )  # 仅有两侧空格时才把字母 x 当乘号，避免改写标识符。
    for index, literal in enumerate(hexadecimal_literals):
        normalized = normalized.replace(f"\ue000{index}\ue001", literal)
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
        f'<div class="nav-location">{_escape(_short_nav_location(_display_change_location(change)))}</div>'
        "</div></a>"
    )


def _render_table_nav_item(index: int, change: TableChange) -> str:
    """Render a table finding in the same navigation surface as prose changes."""

    label = _CHANGE_LABELS.get(change.change_type, change.change_type)
    material_count = len(_material_table_row_changes(change))
    review_count = len(_table_review_rows(change))
    detail_parts = []
    if material_count:
        detail_parts.append(f"{material_count} 行")
    if review_count:
        detail_parts.append(f"{review_count} 项复核")
    detail = " + ".join(detail_parts) or "无行级结果"
    if change.caption_changed:
        detail = f"{detail} + 表题" if change.row_changes else "表题/表号"
    return (
        f'<a class="nav-item nav-{change.change_type}" href="#table-change-{index}">'
        f"<span>T{index}</span>"
        '<div class="nav-body">'
        f'<div class="nav-label">表格{_escape(label)} · {_escape(detail)}</div>'
        f'<div class="nav-location">{_escape(_table_change_title(change))}</div>'
        "</div></a>"
    )


def _short_nav_location(location: str) -> str:
    """Keep the discriminating tail of a deep section path in the narrow sidebar."""

    parts = [part.strip() for part in location.split(" / ") if part.strip()]
    if len(parts) <= 2:
        return location
    return "… / " + " / ".join(parts[-2:])


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
        parts.append(f"{len(_reader_single_list_groups(change.added_snippets))} 段新增")
    if change.removed_snippets:
        parts.append(f"{len(_reader_single_list_groups(change.removed_snippets))} 段删除")
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
    if _change_has_wording_difference(change):
        natures.append("术语/文字")
    return natures[:4]


def _change_has_wording_difference(change: SectionChange) -> bool:
    """Return True when visible word or identifier tokens differ across sides."""

    old_texts = [pair.old for pair in change.replaced_snippets] + change.removed_snippets
    new_texts = [pair.new for pair in change.replaced_snippets] + change.added_snippets
    return _wording_token_keys(old_texts) != _wording_token_keys(new_texts)


def _wording_token_keys(texts: list[str]) -> tuple[str, ...]:
    """Collect generic word-like keys while excluding numeric display variants."""

    return tuple(
        token.key
        for text in texts
        for token in _inline_tokens(text)
        if re.search(r"[a-z\u4e00-\u9fff]", token.key, flags=re.I)
    )


def _render_pair_html(old_text: str, new_text: str) -> str:
    """Render old/new replacement snippets with inline highlighting."""

    old_html, new_html = _inline_diff_html(old_text, new_text)
    difference_hint = _reader_pair_difference_hint(old_text, new_text)
    old_html = _render_collapsible_snippet_html(
        old_text,
        old_html,
        difference_hint=difference_hint,
    )
    new_html = _render_collapsible_snippet_html(
        new_text,
        new_html,
        difference_hint=difference_hint,
    )
    glyph_note = _unverified_pua_mapping_note(old_text, new_text)
    glyph_note_html = (
        f'<div class="match-basis">{_escape(glyph_note)}</div>'
        if glyph_note
        else ""
    )
    return f"""
        {glyph_note_html}
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


def _unverified_pua_mapping_note(
    old_text: str,
    new_text: str,
    *,
    reader_old_text: str | None = None,
    reader_new_text: str | None = None,
    force_reader_equivalent: bool = False,
) -> str:
    """Explain a raw PUA/Unicode delta that looks identical after reader decoding."""

    if old_text == new_text:
        return ""
    if not re.search(r"[\ue000-\uf8ff]", old_text + new_text):
        return ""
    reader_old = (
        reader_old_text
        if reader_old_text is not None
        else readable_symbol_font_glyphs(old_text)
    )
    reader_new = (
        reader_new_text
        if reader_new_text is not None
        else readable_symbol_font_glyphs(new_text)
    )
    if (
        reader_symbol_mapping_key(reader_old)
        != reader_symbol_mapping_key(reader_new)
        and not force_reader_equivalent
    ):
        return ""
    pua_characters = list(
        dict.fromkeys(
            character
            for character in old_text + new_text
            if "\ue000" <= character <= "\uf8ff"
            and readable_symbol_font_glyphs(character) != character
        )
    )
    if not pua_characters:
        return ""
    visible_characters = list(
        dict.fromkeys(
            readable_symbol_font_glyphs(character)
            for character in pua_characters
        )
    )

    def codepoints(characters: Iterable[str]) -> str:
        ordered = dict.fromkeys(f"U+{ord(character):04X}" for character in characters)
        return ", ".join(ordered)

    def relevant_characters(raw_text: str) -> list[str]:
        return list(
            dict.fromkeys(
                character
                for character in raw_text
                if "\ue000" <= character <= "\uf8ff"
                or character in visible_characters
            )
        )

    old_codes = codepoints(relevant_characters(old_text))
    new_codes = codepoints(relevant_characters(new_text))
    visible = "".join(visible_characters)
    if not old_codes or not new_codes:
        return ""
    return (
        f"未验证字体编码：旧码位 {old_codes}，新码位 {new_codes}"
        f"（读者显示为 {visible}）；需人工复核。"
    )


def _render_single_list(title: str, snippets: list[str], css_class: str) -> str:
    """Render added-only or removed-only snippets."""

    if not snippets:
        return ""
    items: list[str] = []
    for snippet in _reader_single_list_groups(snippets):
        marked_html = f'<mark class="{css_class}">{_escape(snippet)}</mark>'
        rendered_html = _render_collapsible_snippet_html(
            snippet,
            marked_html,
            difference_hint=_reader_single_side_evidence_hint(snippet),
        )
        items.append(f"<li>{rendered_html}</li>")
    items_html = "\n".join(items)
    return f"<h4>{title}</h4><ul class=\"single-list\">{items_html}</ul>"


def _reader_single_list_groups(snippets: list[str]) -> list[str]:
    """Fold adjacent diagram labels; prose is already source-grouped by compare."""

    groups: list[str] = []
    diagram_buffer: list[str] = []
    compact_snippets = [
        compact
        for snippet in snippets
        if (compact := compact_inline(snippet))
    ]

    def flush_diagram() -> None:
        if diagram_buffer:
            groups.append("图示标签：" + " / ".join(diagram_buffer))
            diagram_buffer.clear()

    for index, compact in enumerate(compact_snippets):
        diagram_context_reference = bool(
            _reader_snippet_is_isolated_diagram_reference(compact)
            and 0 < index < len(compact_snippets) - 1
            and _reader_snippet_is_diagram_label(compact_snippets[index - 1])
            and _reader_snippet_is_diagram_label(compact_snippets[index + 1])
        )
        if _reader_snippet_is_diagram_label(compact) or diagram_context_reference:
            diagram_buffer.append(compact)
            continue
        flush_diagram()
        groups.append(compact)
    flush_diagram()
    return groups


def _reader_snippet_is_isolated_diagram_reference(value: str) -> bool:
    """Recognize a bare numbered caption only inside proven label neighbors."""

    return bool(
        re.fullmatch(
            rf"(?i)(?:table|figure)\s+\d+(?:\s*{TABLE_NUMBER_DASH_CLASS}\s*\d+)?\.",
            compact_inline(value),
        )
    )


def _render_collapsible_snippet_html(
    value: str,
    body_html: str,
    *,
    difference_hint: str = "",
) -> str:
    """Keep raw evidence available without showing linearized layout text by default."""

    kind = _reader_snippet_collapse_kind(value)
    if kind is None:
        return body_html
    notice = _reader_snippet_notice(
        value,
        kind,
        expandable=True,
        difference_hint=difference_hint,
    )
    visible_tail = _reader_visible_prose_tail(value)
    tail_html = (
        '<div class="snippet-visible-tail"><strong>可读正文：</strong>'
        f"{_escape(visible_tail)}</div>"
        if visible_tail
        else ""
    )
    if kind == "layout":
        notice = _reader_snippet_notice(
            value,
            kind,
            expandable=False,
            difference_hint=difference_hint,
        )
        return (
            '<div class="snippet-folded-layout">'
            f"{_escape(notice)} "
            '<a href="protocol_diff_data.json">查看审计数据</a>'
            "</div>"
            f"{tail_html}"
        )  # 线性化公式/表体不再嵌入 HTML；原文只保留在 JSON/CSV 与源 PDF。
    return (
        f'<details class="snippet-detail snippet-detail-{kind}">'
        f'<summary>{_escape(notice)}</summary>'
        f'<div class="snippet-detail-body">{body_html}</div>'
        "</details>"
        f"{tail_html}"
    )


def _reader_snippet_text(value: str, *, difference_hint: str = "") -> str:
    """Return concise reader text while JSON/CSV retain the complete snippet."""

    kind = _reader_snippet_collapse_kind(value)
    if kind is None:
        return value
    notice = _reader_snippet_notice(
        value,
        kind,
        expandable=False,
        difference_hint=difference_hint,
    )
    visible_tail = _reader_visible_prose_tail(value)
    tail_text = f" 可读正文：{visible_tail}" if visible_tail else ""
    return f"[{notice}]{tail_text}"


def _reader_snippet_collapse_kind(
    value: str,
    *,
    _allow_prose_tail_strip: bool = True,
) -> str | None:
    """Classify snippets that are unreadable when expanded inline."""

    compact = compact_inline(value)
    if not compact:
        return None
    if re.fullmatch(r"[A-Za-zα-ωΑ-Ω]", compact):
        return "layout"  # 孤立单字母是公式/图轴残片，不当作一条可读删除段。
    if _reader_short_formula_fragment(compact):
        return "layout"
    normalized_math = compact.replace("−", "-").replace("–", "-")
    if len(normalized_math) <= 40 and not re.search(
        r"[A-Za-z\u4e00-\u9fff=]",
        normalized_math,
    ):
        math_tokens = re.findall(
            r"[-+]?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?",
            normalized_math,
        )
        math_residue = re.sub(
            r"[-+]?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?",
            "",
            normalized_math,
        )
        if (
            len(math_tokens) >= 3
            and any("/" in token for token in math_tokens)
            and not math_residue.strip(" ,;:()[]{}")
        ):
            return "layout"  # 比较/JSON保留短公式，读者视图折叠孤立线性化残片。
    if _reader_snippet_is_diagram_label(compact):
        return "diagram"
    prose_tail_stripped = False
    short_unknown_formula = _reader_has_short_unknown_pua_formula(compact)
    if _allow_prose_tail_strip:
        tail = _reader_prose_tail_candidate(compact)
        if tail and compact.endswith(tail):
            prefix = compact[: -len(tail)].rstrip()
            minimum_prefix_length = 20 if short_unknown_formula else 80
            if len(prefix) >= minimum_prefix_length:
                compact = prefix  # 先剥离可读规范尾句，再判断前面的强表格/图轴墙。
                prose_tail_stripped = True
    tokens = compact.split()
    numeric_flags = [bool(re.search(r"\d", token)) for token in tokens]
    numeric_token_count = sum(numeric_flags)
    maximum_numeric_run = 0
    current_numeric_run = 0
    for is_numeric in numeric_flags:
        current_numeric_run = current_numeric_run + 1 if is_numeric else 0
        maximum_numeric_run = max(maximum_numeric_run, current_numeric_run)
    header_term_count = len(_READER_LAYOUT_HEADER_RE.findall(compact))
    raw_private_math_glyph_count = len(
        re.findall(r"[\ue000-\uf8ff]", compact)
    )
    decoded_compact = readable_symbol_font_glyphs(compact)
    private_math_glyph_count = len(
        re.findall(r"[\ue000-\uf8ff]", decoded_compact)
    )  # 已确认可解码的 Symbol 字形不是乱码证据；只统计映射后仍未知的私用字符。
    symbol_token_count = sum(
        bool(re.search(r"[<>=≤≥±×*/()]", token))
        for token in tokens
    )
    single_alpha_token_count = sum(
        bool(re.fullmatch(r"[A-Za-z]", token))
        for token in tokens
    )
    numeric_ratio = numeric_token_count / max(len(tokens), 1)
    single_alpha_ratio = single_alpha_token_count / max(len(tokens), 1)
    private_math_glyph_ratio = private_math_glyph_count / max(len(compact), 1)
    has_prose_verb = bool(_READER_PROSE_VERB_RE.search(compact))
    has_normative_verb = bool(_READER_NORMATIVE_VERB_RE.search(compact))
    starts_with_normative_prose = _reader_starts_with_normative_prose(compact)
    axis_marker_count = len(
        re.findall(
            r"(?i)(?<![A-Za-z0-9])(?:freq(?:uency)?|[kmg]hz|dB|figure|curve|plot|axis|"
            r"S(?:DD|DC|CC)\d{2})(?![A-Za-z0-9])",
            compact,
        )
    )
    repeated_single_alpha_pair_count = sum(
        bool(re.fullmatch(r"[A-Za-z]", previous))
        and bool(re.fullmatch(r"[A-Za-z]", current))
        and previous.casefold() == current.casefold()
        for previous, current in zip(tokens, tokens[1:])
    )
    longest_nonsequential_alpha_run = _longest_nonsequential_single_alpha_run(tokens)
    arithmetic_operator_count = len(re.findall(r"[=+\-−–*/≤≥<>]", compact))
    strong_header_layout = (
        header_term_count >= 12
        and numeric_token_count >= 25
        and numeric_ratio >= 0.40
        and not has_normative_verb
    )
    strong_private_layout = (
        len(compact) >= 160
        and private_math_glyph_count >= 2
        and raw_private_math_glyph_count >= 4
        and numeric_token_count >= 12
        and numeric_ratio >= 0.20
        and not starts_with_normative_prose
    )  # 长公式可同时含已知和未知 Symbol 字形；以完整规范主语开头的句子仍不折叠。
    strong_mixed_table_layout = (
        len(compact) >= 400
        and header_term_count >= 10
        and numeric_token_count >= 20
        and symbol_token_count >= 8
        and not has_normative_verb
    )
    strong_axis_layout = (
        len(compact) >= (140 if prose_tail_stripped else 250)
        and numeric_token_count >= 20
        and numeric_ratio >= 0.33
        and maximum_numeric_run >= 12
        and axis_marker_count >= (1 if prose_tail_stripped else 2)
        and not starts_with_normative_prose
    )  # 可读尾句剥离后图轴会缩短；长数字刻度+至少一个 Figure/freq 锚点仍是强证据。
    strong_spaced_label_layout = (
        len(compact) >= 120
        and single_alpha_token_count >= 20
        and single_alpha_ratio >= 0.35
        and numeric_token_count >= 6
        and (
            repeated_single_alpha_pair_count >= 6
            or longest_nonsequential_alpha_run >= 16
        )
    )
    strong_equation_layout = (
        len(compact) >= 100
        and numeric_token_count >= 8
        and arithmetic_operator_count >= 6
        and (private_math_glyph_count >= 1 or single_alpha_token_count >= 4)
        and not has_prose_verb
    )
    strong_short_private_formula = (
        short_unknown_formula or _reader_has_short_unknown_pua_formula(compact)
    )  # 可读 where 尾句剥离后不应丢掉剥离前的公式证据。
    strong_layout_evidence = (
        strong_header_layout
        or strong_private_layout
        or strong_mixed_table_layout
        or strong_axis_layout
        or strong_spaced_label_layout
        or strong_equation_layout
        or strong_short_private_formula
    )  # 复合强证据可越过片段尾部的一句解释性 prose；任一单特征仍不足以折叠正常条款。
    looks_linearized = (
        strong_layout_evidence
        or
        (
            len(compact) >= _READER_LAYOUT_SNIPPET_MIN_CHARS
            and (
                (
                    header_term_count >= 3
                    and numeric_token_count >= 4
                    and not has_prose_verb
                )
                or (
                    numeric_token_count >= 12
                    and numeric_ratio >= 0.25
                    and not has_prose_verb
                )
                or (
                    symbol_token_count >= 8
                    and numeric_token_count >= 4
                    and not has_prose_verb
                )
            )
        )
        or (
            len(compact) >= 30
            and private_math_glyph_count >= 2
            and raw_private_math_glyph_count >= 4
            and (private_math_glyph_ratio >= 0.04 or numeric_token_count >= 2)
            and not has_prose_verb
        )  # 多个 Symbol 私用字形在短公式中也会形成乱码，不受长表格门槛限制。
    )
    if looks_linearized:
        return "layout"
    return None


def _reader_short_formula_fragment(value: str) -> bool:
    """Recognize a short identifier followed only by orphan formula variables."""

    compact = compact_inline(value)
    if not compact or len(compact) > 80:
        return False
    if _READER_NORMATIVE_VERB_RE.search(compact) or _READER_PROSE_VERB_RE.search(compact):
        return False
    tokens = compact.split()
    if len(tokens) < 3:
        return False
    technical_identifiers = [
        token
        for token in tokens
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+", token)
        or (
            re.fullmatch(r"[A-Za-z]+[0-9][A-Za-z0-9_]*", token)
            and len(token) >= 4
        )
    ]
    variable_tokens = [token for token in tokens if re.fullmatch(r"[A-Za-z]{1,2}", token)]
    return (
        bool(technical_identifiers)
        and len(variable_tokens) >= 2
        and len(technical_identifiers) + len(variable_tokens) == len(tokens)
    )  # `Bd_12DDS f f f`/`fx bx FFE_Post` 是游离下标墙；完整短句和限值不会满足该形状。


def _reader_starts_with_normative_prose(value: str) -> bool:
    """Protect a sentence-level requirement without blocking Figure/curve labels."""

    candidate = compact_inline(value)
    if not candidate:
        return False
    match = _READER_LEADING_NORMATIVE_RE.search(candidate[:180])
    if match is None:
        return False
    first_token = candidate.split(maxsplit=1)[0]
    after_first_token = candidate[len(first_token) :].lstrip()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", first_token) and re.match(
        r"[=≤≥<>+*/]",
        after_first_token,
    ):
        return False  # SNDR = ... should ... 仍是以公式开头，不能因尾部动词被当成规范段落。
    prose_tokens = re.findall(
        r"[A-Za-z]{2,}|[\u3400-\u4dbf\u4e00-\u9fff]+",
        candidate[: match.end()],
    )
    return len(prose_tokens) >= 2


def _reader_snippet_is_diagram_label(value: str) -> bool:
    """Recognize only short, non-sentence labels typical of a figure drawing."""

    compact = compact_inline(value)
    if not compact or len(compact) > 160:
        return False
    if compact.startswith("图示标签："):
        return True
    if compact.endswith((".", "!", "?", "。", "！", "？")):
        return False
    if re.match(r"(?i)^(?:table|figure|equation|section)\b", compact):
        return False
    if _READER_NORMATIVE_VERB_RE.search(compact) or _READER_PROSE_VERB_RE.search(compact):
        return False
    if _READER_DIAGRAM_LABEL_SINGLE_RE.fullmatch(compact):
        return True
    if re.fullmatch(r"(?i)(?:stressed signal|sinusoidal interface|DC block)", compact):
        return True
    word_count = len(re.findall(r"[A-Za-z][A-Za-z0-9-]*", compact))
    if word_count > 12:
        return False
    markers = _READER_DIAGRAM_LABEL_TOKEN_RE.findall(compact)
    return len(markers) >= 2


def _reader_snippet_notice(
    value: str,
    kind: str,
    *,
    expandable: bool,
    difference_hint: str = "",
) -> str:
    """Build the visible summary for a folded reader snippet."""

    character_count = len(compact_inline(value))
    if kind == "layout":
        preview = _reader_snippet_preview(value)
        evidence_hint = (
            "可展开原始文字，优先核对表格补充证据或源 PDF。"
            if expandable
            else "完整文字见 JSON/CSV 审计文件或源 PDF。"
        )
        notice = (
            f"疑似表格或公式的版面文字已折叠（{character_count} 字）；"
            f"开头“{preview}”；{evidence_hint}"
        )
        if difference_hint:
            notice += f" {difference_hint}"
        return notice
    if kind == "diagram":
        compact = compact_inline(value)
        label_count = (
            len([part for part in compact.split("：", 1)[1].split(" / ") if part])
            if compact.startswith("图示标签：")
            else 1
        )
        evidence_hint = (
            "可展开查看原始标签，图形内容请核对源 PDF。"
            if expandable
            else "原始标签见 JSON/CSV 审计文件或源 PDF。"
        )
        notice = f"图示中的短标签已合并折叠（{label_count} 项）；{evidence_hint}"
        if difference_hint:
            notice += f" {difference_hint}"
        return notice
    raise ValueError(f"未知读者片段类型: {kind}")


def _longest_nonsequential_single_alpha_run(tokens: list[str]) -> int:
    """Measure broken vertical-label spelling without folding A..V lane lists."""

    longest = 0
    current: list[str] = []
    for token in [*tokens, ""]:
        if re.fullmatch(r"[A-Za-z]", token):
            current.append(token)
            continue
        if current and not _is_monotonic_alpha_sequence(current):
            longest = max(longest, len(current))
        current = []
    return longest


def _is_monotonic_alpha_sequence(tokens: list[str]) -> bool:
    """Return True for ascending or descending adjacent alphabet labels."""

    values = [ord(token.casefold()) for token in tokens]
    if len(values) < 2:
        return True
    steps = [current - previous for previous, current in zip(values, values[1:])]
    return all(step == 1 for step in steps) or all(step == -1 for step in steps)


def _reader_prose_tail_candidates(value: str) -> list[str]:
    """Return readable-looking suffix candidates without classifying their layout."""

    compact = compact_inline(value)
    short_unknown_formula = _reader_has_short_unknown_pua_formula(compact)
    if (
        len(compact) < _READER_LAYOUT_SNIPPET_MIN_CHARS
        and not short_unknown_formula
    ):
        return []
    minimum_prefix_length = 20 if short_unknown_formula else 80
    minimum_tail_length = 10 if short_unknown_formula else 30
    minimum_tail_letters = 6 if short_unknown_formula else 24
    candidates: list[str] = []
    starts = [match.end() for match in _READER_PROSE_TAIL_START_RE.finditer(compact)]
    for start in starts:
        prefix = compact[:start].strip()
        tail = compact[start:].strip()
        if len(prefix) < minimum_prefix_length or len(tail) < minimum_tail_length:
            continue
        if not _READER_PROSE_VERB_RE.search(tail):
            continue
        if (
            len(re.findall(r"[A-Za-z\u3400-\u4dbf\u4e00-\u9fff]", tail))
            < minimum_tail_letters
        ):
            continue
        candidates.append(tail)
    specific = [
        tail
        for tail in candidates
        if re.match(r"(?i)^(?:where\s+port\b|editor[’']s\s+note:)", tail)
    ]
    return [*specific, *(tail for tail in candidates if tail not in specific)]


def _reader_has_short_unknown_pua_formula(value: str) -> bool:
    """Recognize a compact formula wall even when its prose tail contains ``is``."""

    compact = compact_inline(value)
    if len(compact) < 24 or _reader_starts_with_normative_prose(compact):
        return False
    decoded = readable_symbol_font_glyphs(compact)
    unknown_private_count = len(re.findall(r"[\ue000-\uf8ff]", decoded))
    raw_private_count = len(re.findall(r"[\ue000-\uf8ff]", compact))
    numeric_count = len(re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", compact))
    operator_count = len(re.findall(r"[=+\-−–*/≤≥<>]", compact))
    return (
        unknown_private_count >= 4
        and raw_private_count >= 4
        and numeric_count >= 2
        and "=" in compact
        and operator_count >= 3
    )


def _reader_prose_tail_candidate(value: str) -> str:
    """Return the longest plausible prose suffix for prefix-only layout checks."""

    candidates = _reader_prose_tail_candidates(value)
    return candidates[0] if candidates else ""


def _reader_visible_prose_tail(value: str) -> str:
    """Keep a readable normative suffix outside a folded table/formula wall."""

    for tail in _reader_prose_tail_candidates(value):
        if _reader_snippet_collapse_kind(
            tail,
            _allow_prose_tail_strip=False,
        ) is not None:
            continue  # 后缀自身仍是版面墙时不能把它重新展开到读者层。
        return tail
    return ""


def _reader_snippet_preview(value: str, max_chars: int = 72) -> str:
    """Keep one short distinguishing anchor without recreating the text wall."""

    compact = compact_inline(readable_symbol_font_glyphs(value))
    if _reader_short_formula_fragment(compact):
        technical_anchor = re.search(
            r"(?<![A-Za-z0-9_])(?=[A-Za-z0-9_]*[A-Z0-9_])"
            r"[A-Za-z][A-Za-z0-9_]{2,}(?![A-Za-z0-9_])",
            compact,
        )
        return (
            f"可辨识标记 {technical_anchor.group(0)}"
            if technical_anchor
            else "短公式或下标片段"
        )
    if re.search(r"[\ue000-\uf8ff]", compact):
        printable = compact_inline(re.sub(r"[\ue000-\uf8ff]+", " ", compact))
        ascii_anchor = re.search(
            r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_.-]{2,}",
            printable,
        )
        return (
            f"可辨识标记 {ascii_anchor.group(0)}"
            if ascii_anchor
            else "无可靠连续正文锚点"
        )
    if len(compact) <= max_chars:
        return compact
    cut = compact.rfind(" ", 0, max_chars + 1)
    if cut < max_chars // 2:
        cut = max_chars
    return compact[:cut].rstrip() + "…"


def _reader_single_side_evidence_hint(value: str) -> str:
    """Expose compact limit/assignment anchors from a folded add/delete wall."""

    if _reader_snippet_collapse_kind(value) != "layout":
        return ""
    compact = compact_inline(readable_symbol_font_glyphs(value))
    anchors = [
        compact_inline(match.group(0))
        for match in re.finditer(
            r"(?i)(?<![A-Za-z0-9_])"
            r"[A-Za-z][A-Za-z0-9_.-]{2,}\s*"
            r"(?:=|<=|>=|≤|≥|<|>)\s*"
            r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
            r"(?:\s*(?:dB|UI|mV|V|ps|ns|ppm|MHz|GHz|Ω|ohm))?",
            compact,
        )
    ]
    unique_anchors = list(dict.fromkeys(anchors))[-3:]
    if not unique_anchors:
        return ""
    return "关键锚点：" + "；".join(f"“{anchor}”" for anchor in unique_anchors)


def _reader_pair_difference_hint(old_text: str, new_text: str) -> str:
    """Expose compact changed anchors even when both raw layout walls stay folded."""

    if _reader_pair_is_layout_token_reorder(old_text, new_text):
        return "两侧公式/图表 token 及出现次数相同，仅线性提取顺序不同；需核对源 PDF。"

    old_tokens = _inline_tokens(old_text)
    new_tokens = _inline_tokens(new_text)
    matcher = difflib.SequenceMatcher(
        None,
        [token.key for token in old_tokens],
        [token.key for token in new_tokens],
        autojunk=False,
    )
    candidates: list[tuple[int, int, int, str]] = []
    for order, (tag, old_start, old_end, new_start, new_end) in enumerate(
        matcher.get_opcodes()
    ):
        if tag == "equal":
            continue
        old_anchor_tokens = _reader_anchor_tokens_with_attached_prefix(
            old_text,
            old_tokens,
            old_start,
            old_end,
        )
        new_anchor_tokens = _reader_anchor_tokens_with_attached_prefix(
            new_text,
            new_tokens,
            new_start,
            new_end,
        )
        old_anchor = _reader_difference_anchor(old_text, old_anchor_tokens)
        new_anchor = _reader_difference_anchor(new_text, new_anchor_tokens)
        changed_token_count = (old_end - old_start) + (new_end - new_start)
        technical_priority = _reader_difference_technical_priority(
            old_tokens[old_start:old_end],
            new_tokens[new_start:new_end],
            old_context=old_tokens[max(0, old_start - 2) : min(len(old_tokens), old_end + 3)],
            new_context=new_tokens[max(0, new_start - 2) : min(len(new_tokens), new_end + 3)],
        )
        candidates.append(
            (
                technical_priority,
                changed_token_count,
                order,
                f"旧“{old_anchor}” → 新“{new_anchor}”",
            )
        )

    if not candidates:
        return ""
    # Numeric/engineering anchors are review-critical; short changes and document order break ties.
    selected = sorted(candidates)[:3]
    selected.sort(key=lambda candidate: candidate[2])
    count_note = (
        f"（共 {len(candidates)} 处，显示 {len(selected)} 处）"
        if len(candidates) > len(selected)
        else ""
    )
    return (
        f"差异锚点{count_note}："
        + "；".join(candidate[3] for candidate in selected)
    )


def _reader_table_changes(changes: list[TableChange]) -> list[TableChange]:
    """Remove generic flat-structure reminders from reader formats only.

    The raw table model, JSON, and CSV retain the reminder.  A reader does not
    need a standalone card that merely says row/column boundaries were not
    verified when both table sides exist and no actual row or caption fact is
    present.  Mixed cards keep every concrete row fact.
    """

    reader_changes: list[TableChange] = []
    for change in changes:
        # 出版历史类表格仍写入原始 JSON/CSV，但不占用面向技术读者的表格证据区。
        if change.role == "document_metadata":
            continue
        can_hide_generic_review = bool(
            change.old_tables
            and change.new_tables
            and not change.caption_changed
        )
        row_changes = tuple(
            row
            for row in change.row_changes
            if not (
                can_hide_generic_review
                and row.item == "表格结构复核"
                and row.change_type == "需人工复核"
            )
        )
        if not row_changes and change.change_type == "review":
            if not can_hide_generic_review:
                continue
            row_changes = (
                TableRowChange(
                    item="表格行列结构",
                    old_value=_reader_table_structure_status(change.old_tables),
                    new_value=_reader_table_structure_status(change.new_tables),
                    change_type="需人工复核",
                ),
            )  # 隐去冗长抽取告警，但保留紧凑复核事实，禁止把未知结构伪装成全量一致。
        change_type = change.change_type
        if (
            change.old_tables
            and change.new_tables
            and row_changes
            and not change.caption_changed
            and all(row.change_type == "需人工复核" for row in row_changes)
        ):
            change_type = "review"
        reader_changes.append(
            replace(change, change_type=change_type, row_changes=row_changes)
        )
    return reader_changes


def _reader_table_structure_status(tables: tuple[TableVisual, ...]) -> str:
    """Summarize structural certainty without exposing extractor diagnostics."""

    if not tables:
        return "无对应表格"
    flat_count = sum(table.ocr_status == "text_backed_exact_match" for table in tables)
    if flat_count == len(tables):
        return "行列边界未验证"
    if flat_count:
        return "部分行列边界未验证"
    return "行列边界已识别"


def _reader_section_change(
    change: SectionChange,
    table_evidence: (
        list[TableChange | _TableVisualGroup]
        | tuple[TableChange | _TableVisualGroup, ...]
    ) = (),
) -> SectionChange | None:
    """Return reader-only classification without mutating raw audit facts."""

    if _reader_change_is_coordinate_proven_table_body_duplicate(change, table_evidence):
        return None
    # 混合章节不能整卡删除；只剔除由同章节完整表格截图逐片段证明的重复表体。
    change = _reader_change_without_evidenced_table_body_fragments(
        change,
        table_evidence,
    )
    if change is None:
        return None
    change = _reader_change_without_evidenced_table_caption_fragments(
        change,
        table_evidence,
    )
    if change is None:
        return None
    change = _reader_change_without_covered_standalone_table_references(change)
    if change is None:
        return None
    if _reader_change_is_layout_reorder_only(change):
        return replace(change, change_type="review")
    return change


def _reader_change_without_evidenced_table_body_fragments(
    change: SectionChange,
    table_evidence: (
        list[TableChange | _TableVisualGroup]
        | tuple[TableChange | _TableVisualGroup, ...]
    ),
) -> SectionChange | None:
    """Hide only mixed-section snippets already preserved by a visible table card.

    The proof is deliberately stricter than generic text similarity: both PDF
    sides need the same paired caption, complete bbox character coverage,
    section/page containment, and either reliable rows or an explicit table
    review row.  Raw ``SectionChange`` data is never mutated; this helper only
    builds the reader copy used by HTML/Markdown/TXT.
    """

    # 单侧新增/删除没有成对章节坐标，不能调用只适用于 paired modified section 的证明函数。
    if (
        change.change_type != "modified"
        or change.old_section is None
        or change.new_section is None
    ):
        return change
    # 复用整卡去重的坐标/标题/完整性门禁，避免远处或不完整截图误删正文。
    eligible_evidence = [
        evidence
        for evidence in table_evidence
        if _reader_table_change_proves_section_duplicate(change, evidence)
    ]
    if not eligible_evidence:
        return change
    # 每一侧只与自己的表格审计文字比对，页码平移不会造成跨版本误覆盖。
    old_table_text = " ".join(
        _reader_table_group_audit_text(evidence.old_tables)
        for evidence in eligible_evidence
    )
    new_table_text = " ".join(
        _reader_table_group_audit_text(evidence.new_tables)
        for evidence in eligible_evidence
    )
    old_table_text_by_page = _reader_table_text_by_page(
        table
        for evidence in eligible_evidence
        for table in evidence.old_tables
    )
    new_table_text_by_page = _reader_table_text_by_page(
        table
        for evidence in eligible_evidence
        for table in evidence.new_tables
    )
    # 可见列表和完整审计列表分别过滤，之后重算真正仍未展示的读者片段数。
    removed = _reader_filter_evidenced_table_fragments(
        change.removed_snippets,
        old_table_text,
        section=change.old_section,
        table_text_by_page=old_table_text_by_page,
    )
    added = _reader_filter_evidenced_table_fragments(
        change.added_snippets,
        new_table_text,
        section=change.new_section,
        table_text_by_page=new_table_text_by_page,
    )
    replaced = _reader_filter_evidenced_table_pairs(
        change.replaced_snippets,
        old_table_text=old_table_text,
        new_table_text=new_table_text,
        old_section=change.old_section,
        new_section=change.new_section,
        old_table_text_by_page=old_table_text_by_page,
        new_table_text_by_page=new_table_text_by_page,
    )
    audit_removed = _reader_filter_evidenced_table_fragments(
        _audit_removed_snippets(change),
        old_table_text,
        section=change.old_section,
        table_text_by_page=old_table_text_by_page,
    )
    audit_added = _reader_filter_evidenced_table_fragments(
        _audit_added_snippets(change),
        new_table_text,
        section=change.new_section,
        table_text_by_page=new_table_text_by_page,
    )
    audit_replaced = _reader_filter_evidenced_table_pairs(
        _audit_replaced_snippets(change),
        old_table_text=old_table_text,
        new_table_text=new_table_text,
        old_section=change.old_section,
        new_section=change.new_section,
        old_table_text_by_page=old_table_text_by_page,
        new_table_text_by_page=new_table_text_by_page,
    )
    # 原先被表格噪声挤出展示上限的正常正文，要从完整 occurrence 中补回原有展示容量。
    if change.audit_removed_snippets is not None:
        removed = _reader_refill_visible_occurrences(
            removed,
            audit_removed,
            limit=len(change.removed_snippets),
        )
    if change.audit_added_snippets is not None:
        added = _reader_refill_visible_occurrences(
            added,
            audit_added,
            limit=len(change.added_snippets),
        )
    if change.audit_replaced_snippets is not None:
        replaced = _reader_refill_visible_occurrences(
            replaced,
            audit_replaced,
            limit=len(change.replaced_snippets),
        )
    # 原比较器的展示上限跨 added/removed/replaced 共用；某一类表格噪声释放的
    # 槽位也必须能补回另一类高价值正文，不能继续显示虚假的 omitted 数量。
    removed, added, replaced = _reader_refill_across_change_kinds(
        removed=removed,
        added=added,
        replaced=replaced,
        audit_removed=audit_removed,
        audit_added=audit_added,
        audit_replaced=audit_replaced,
        limit=(
            len(change.removed_snippets)
            + len(change.added_snippets)
            + len(change.replaced_snippets)
        ),
    )
    # 新版比较器提供三份完整 occurrence 列表时，可安全去掉仅由已隐藏表格行造成的“未展示”提示。
    if all(
        audit is not None
        for audit in (
            change.audit_added_snippets,
            change.audit_removed_snippets,
            change.audit_replaced_snippets,
        )
    ):
        omitted_snippet_count = max(
            0,
            len(audit_added)
            + len(audit_removed)
            + len(audit_replaced)
            - len(added)
            - len(removed)
            - len(replaced),
        )
    else:
        # 旧调用方只有可见列表时缺少重算依据，必须保留原省略计数而不是猜测为零。
        omitted_snippet_count = change.omitted_snippet_count
    cleaned = replace(
        change,
        removed_snippets=removed,
        added_snippets=added,
        replaced_snippets=replaced,
        omitted_snippet_count=omitted_snippet_count,
        audit_removed_snippets=(
            audit_removed if change.audit_removed_snippets is not None else None
        ),
        audit_added_snippets=(
            audit_added if change.audit_added_snippets is not None else None
        ),
        audit_replaced_snippets=(
            audit_replaced if change.audit_replaced_snippets is not None else None
        ),
    )
    # 一张只含表格重复文字的卡片已由下方截图完整替代，不再留空壳或折叠提示。
    if (
        not cleaned.removed_snippets
        and not cleaned.added_snippets
        and not cleaned.replaced_snippets
        and cleaned.omitted_snippet_count == 0
    ):
        return None
    return cleaned


def _reader_refill_visible_occurrences(
    visible: list[str] | list[SnippetPair],
    audit: list[str] | list[SnippetPair],
    *,
    limit: int,
) -> list[str] | list[SnippetPair]:
    """Refill reader slots from the cleaned audit without losing duplicates."""

    if limit <= 0 or len(visible) >= limit:
        return visible[:limit]
    # Counter 按 occurrence 计数；相同表述出现两次时仍可补足两条，不被集合去重吞掉。
    selected = list(visible)
    selected_counts = Counter(selected)
    audit_counts = Counter(audit)
    for item in audit:
        if len(selected) >= limit:
            break
        if selected_counts[item] >= audit_counts[item]:
            continue
        selected.append(item)
        selected_counts[item] += 1
    return selected


def _reader_refill_across_change_kinds(
    *,
    removed: list[str],
    added: list[str],
    replaced: list[SnippetPair],
    audit_removed: list[str],
    audit_added: list[str],
    audit_replaced: list[SnippetPair],
    limit: int,
) -> tuple[list[str], list[str], list[SnippetPair]]:
    """Use reader capacity released by one delta kind for another kind."""

    remaining = max(0, limit - len(removed) - len(added) - len(replaced))
    if remaining == 0:
        return removed, added, replaced

    def append_missing(
        selected: list[str] | list[SnippetPair],
        audit: list[str] | list[SnippetPair],
    ) -> None:
        nonlocal remaining
        selected_counts = Counter(selected)
        audit_counts = Counter(audit)
        for item in audit:
            if remaining == 0:
                break
            if selected_counts[item] >= audit_counts[item]:
                continue
            selected.append(item)
            selected_counts[item] += 1
            remaining -= 1

    # 替换同时提供旧值和新值，信息密度最高；其后按新增、删除补足剩余槽位。
    append_missing(replaced, audit_replaced)
    append_missing(added, audit_added)
    append_missing(removed, audit_removed)
    return removed, added, replaced


def _reader_table_text_by_page(tables: Iterable[TableVisual]) -> dict[int, str]:
    """Group losslessly serialized table evidence by its physical PDF page."""

    parts: dict[int, list[str]] = {}
    seen: set[tuple[object, ...]] = set()
    for table in tables:
        physical_key = (
            table.page_number,
            table.table_number,
            compact_inline(table.title).casefold(),
            tuple(round(value, 3) for value in table.bbox),
            tuple(compact_inline(row) for row in table.row_texts),
        )
        if physical_key in seen:
            continue  # 同一表可能同时来自 TableChange 和未变化配对组，只能计一次 occurrence。
        seen.add(physical_key)
        parts.setdefault(table.page_number, []).append(
            _reader_table_group_audit_text((table,))
        )
    return {
        page_number: " ".join(part for part in page_parts if compact_inline(part))
        for page_number, page_parts in parts.items()
    }


def _reader_table_text_for_snippet(
    snippet: str,
    *,
    section: Section | None,
    table_text: str,
    table_text_by_page: dict[int, str] | None,
) -> str:
    """Bind one snippet to same-page table evidence before allowing suppression."""

    if section is None or table_text_by_page is None:
        return table_text
    if not table_text_by_page:
        return ""
    if not section.page_bodies:
        # Legacy/synthetic single-page sections have an unambiguous page; a
        # multi-page section without page provenance must fail closed.
        if section.start_page != section.end_page:
            return ""
        return table_text_by_page.get(section.start_page, "")

    compact = compact_inline(snippet).casefold()
    if not compact:
        return ""
    page_bodies = {
        page_number: compact_inline(body).casefold()
        for page_number, body in section.page_bodies
    }
    occurrence_pages = {
        page_number
        for page_number, body in page_bodies.items()
        if compact in body
    }
    if not occurrence_pages:
        # Long review units may be rewrapped or symbol-normalized after page
        # extraction.  A high same-page token coverage can recover that page,
        # while the all-occurrences check below still rejects remote ambiguity.
        snippet_counts = Counter(
            token.casefold() for token in _reader_table_body_tokens(snippet)
        )
        required = sum(snippet_counts.values())
        if required >= 8:
            occurrence_pages = {
                page_number
                for page_number, body in page_bodies.items()
                if sum(
                    (
                        snippet_counts
                        & _reader_table_text_index(body).token_counts
                    ).values()
                )
                / required
                >= 0.85
            }
    if not occurrence_pages:
        # A review unit can straddle one physical page break; bind it only when
        # no single page already contains it and both contributing pages carry
        # table evidence.
        page_numbers = sorted(page_bodies)
        for left_page, right_page in zip(page_numbers, page_numbers[1:]):
            if right_page != left_page + 1:
                continue
            if compact in f"{page_bodies[left_page]} {page_bodies[right_page]}":
                occurrence_pages.update((left_page, right_page))
    evidence_pages = set(table_text_by_page)
    if not occurrence_pages or not occurrence_pages <= evidence_pages:
        return ""  # 同文还出现在远处非表格页时，无法证明当前 occurrence 的来源。
    return " ".join(
        table_text_by_page[page_number]
        for page_number in sorted(occurrence_pages)
        if table_text_by_page.get(page_number)
    )


def _reader_evidenced_fragment_flags(
    snippets: list[str],
    *,
    section: Section | None,
    table_text: str,
    table_text_by_page: dict[int, str] | None,
) -> tuple[list[bool], list[str]]:
    """Prove fragments and consume duplicate text occurrences conservatively."""

    bound_texts = [
        _reader_table_text_for_snippet(
            snippet,
            section=section,
            table_text=table_text,
            table_text_by_page=table_text_by_page,
        )
        for snippet in snippets
    ]
    candidates = [
        bool(bound_text)
        and _reader_snippet_is_evidenced_table_fragment(snippet, bound_text)
        for snippet, bound_text in zip(snippets, bound_texts)
    ]
    keys = [compact_inline(snippet).casefold() for snippet in snippets]
    multiplicity = Counter(
        key for key, candidate in zip(keys, candidates) if candidate and key
    )
    capacities: dict[tuple[str, str], int] = {}
    used: Counter[tuple[str, str]] = Counter()
    flags: list[bool] = []
    for index, (candidate, key, bound_text) in enumerate(
        zip(candidates, keys, bound_texts)
    ):
        if not candidate or not key:
            flags.append(False)
            continue
        budget_key = (compact_inline(bound_text).casefold(), key)
        if multiplicity[key] > 1 and budget_key not in capacities:
            exact_count = budget_key[0].count(key)
            # Structured rows insert `=` and move values into another field,
            # so literal counting can understate a short row fragment.  The
            # exact token multiset supplies the remaining occurrence budget.
            demand = Counter(
                token.casefold()
                for token in _reader_table_body_tokens(snippets[index])
                if re.search(r"[A-Za-z0-9\u3400-\u4dbf\u4e00-\u9fff]", token)
            )
            available = _reader_table_text_index(bound_text).token_counts
            token_capacity = min(
                (available[token] // count for token, count in demand.items()),
                default=0,
            )
            capacities[budget_key] = max(1, exact_count, token_capacity)
        capacity = capacities.get(budget_key, 1)
        if multiplicity[key] > 1 and used[budget_key] >= capacity:
            flags.append(False)
            continue
        used[budget_key] += 1
        flags.append(True)
    return flags, bound_texts


def _reader_filter_evidenced_table_fragments(
    snippets: list[str],
    table_text: str,
    *,
    section: Section | None = None,
    table_text_by_page: dict[int, str] | None = None,
) -> list[str]:
    """Drop covered table text while preserving a readable suffix from the same snippet."""

    # 先对完整有序列表做强证据判定，短桥接标签只能依赖相邻两条已证明表格行。
    strongly_covered, bound_texts = _reader_evidenced_fragment_flags(
        snippets,
        section=section,
        table_text=table_text,
        table_text_by_page=table_text_by_page,
    )
    kept: list[str] = []
    for index, snippet in enumerate(snippets):
        if strongly_covered[index]:
            # PDF extractors can concatenate a long table body and the following
            # normative sentence.  Keep only that readable suffix instead of
            # deleting the whole occurrence or restoring the table wall.
            prose_tail = _reader_visible_prose_tail(snippet)
            if prose_tail:
                kept.append(prose_tail)
            continue
        # `DC`/`DC2` 之类一两个词本身不可授权删除；夹在两个已覆盖表格行之间时才视为拆行残片。
        if (
            0 < index < len(snippets) - 1
            and strongly_covered[index - 1]
            and strongly_covered[index + 1]
            and _reader_tiny_table_bridge_is_covered(snippet, bound_texts[index])
        ):
            continue
        kept.append(snippet)
    return kept


def _reader_filter_evidenced_table_pairs(
    pairs: list[SnippetPair],
    *,
    old_table_text: str,
    new_table_text: str,
    old_section: Section | None = None,
    new_section: Section | None = None,
    old_table_text_by_page: dict[int, str] | None = None,
    new_table_text_by_page: dict[int, str] | None = None,
) -> list[SnippetPair]:
    """Drop a replacement only when both complete sides are table-backed."""

    old_flags, _old_bound_texts = _reader_evidenced_fragment_flags(
        [pair.old for pair in pairs],
        section=old_section,
        table_text=old_table_text,
        table_text_by_page=old_table_text_by_page,
    )
    new_flags, _new_bound_texts = _reader_evidenced_fragment_flags(
        [pair.new for pair in pairs],
        section=new_section,
        table_text=new_table_text,
        table_text_by_page=new_table_text_by_page,
    )
    kept: list[SnippetPair] = []
    for pair, old_covered, new_covered in zip(pairs, old_flags, new_flags):
        # 任一侧仍含未证明正文时保留整对，避免把真实术语或限值修改拆丢。
        if not (old_covered and new_covered):
            kept.append(pair)
            continue
        old_tail = _reader_visible_prose_tail(pair.old)
        new_tail = _reader_visible_prose_tail(pair.new)
        if old_tail and new_tail:
            if compact_inline(old_tail) != compact_inline(new_tail):
                kept.append(SnippetPair(old_tail, new_tail))
            continue
        if old_tail or new_tail:
            # 不制造 `symbol=<empty>` 一类单侧替换；无法对称裁剪时保留原始事实。
            kept.append(pair)
    return kept


def _reader_snippet_is_evidenced_table_fragment(
    snippet: str,
    table_text: str,
) -> bool:
    """Require exact-token table coverage plus a layout/row-shaped fragment."""

    compact = compact_inline(snippet)
    table_index = _reader_table_text_index(table_text)
    if not compact or not table_index.compact:
        return False
    collapse_kind = _reader_snippet_collapse_kind(compact)
    # 可读规范句即使出现在表格附近也继续展示；长线性化表体由 layout 强证据单独处理。
    if collapse_kind != "layout" and _READER_PROSE_VERB_RE.search(compact):
        return False
    # 字段标签的大小写来自序列化格式而非技术语义；覆盖比较统一 casefold，但仍保留数字和符号形态。
    snippet_tokens = [
        token.casefold() for token in _reader_table_body_tokens(compact)
    ]
    if not snippet_tokens or not table_index.token_counts:
        return False
    shared_count = sum(
        (Counter(snippet_tokens) & table_index.token_counts).values()
    )
    coverage = shared_count / len(snippet_tokens)
    if collapse_kind == "layout" and len(compact) >= _READER_LAYOUT_SNIPPET_MIN_CHARS:
        return len(snippet_tokens) >= 12 and shared_count >= 10 and coverage >= 0.65
    # 短表格行常被多行单元格拆开；字段词或数值形态加高 token 覆盖可证明其来自结构化表体。
    header_count = len(_READER_LAYOUT_HEADER_RE.findall(compact))
    has_numeric_or_operator = bool(re.search(r"\d|[=<>≤≥±×]", compact))
    has_row_label = bool(
        re.search(
            r"(?i)\b(?:minimum|maximum|min|max|step(?:\s+size)?|parameter|symbol|value|units?)\b",
            compact,
        )
    )
    row_shape_proven = bool(
        header_count >= 2
        or has_row_label
        or (len(snippet_tokens) >= 5 and coverage >= 0.85)
        or (
            has_numeric_or_operator
            and len(snippet_tokens) >= 5
        )
    )
    return bool(
        len(snippet_tokens) >= 3
        and shared_count >= 3
        and row_shape_proven
        and (
            compact.casefold() in table_index.compact_casefold
            or coverage >= 0.75
        )
    )


@dataclass(frozen=True)
class _ReaderTableTextIndex:
    """Cached normalization for one immutable serialized table evidence string."""

    compact: str
    compact_casefold: str
    token_counts: Counter[str]


@lru_cache(maxsize=256)
def _reader_table_text_index(table_text: str) -> _ReaderTableTextIndex:
    """Tokenize a table body once even when visible and audit snippets reuse it."""

    compact = compact_inline(table_text)
    return _ReaderTableTextIndex(
        compact=compact,
        compact_casefold=compact.casefold(),
        token_counts=Counter(
            token.casefold() for token in _reader_table_body_tokens(table_text)
        ),
    )


def _reader_tiny_table_bridge_is_covered(snippet: str, table_text: str) -> bool:
    """Recognize one/two-token split labels only inside two proven table rows."""

    tokens = [token.casefold() for token in _reader_table_body_tokens(snippet)]
    if not 1 <= len(tokens) <= 2:
        return False
    compact = compact_inline(snippet)
    # 单字母通常是独立公式/变量，不能因恰好夹在表格片段间而删除；这里只
    # 接受 DC、DC2、R0 一类至少两字符且带大写/数字形态的行标签。
    if not re.fullmatch(r"(?:[A-Z][A-Za-z0-9_]{1,15})(?:\s+[A-Z][A-Za-z0-9_]{1,15})?", compact):
        return False
    table_counts = Counter(
        token.casefold() for token in _reader_table_body_tokens(table_text)
    )
    return bool(
        not (Counter(tokens) - table_counts)
        or compact.casefold() in compact_inline(table_text).casefold()
    )


def _reader_change_without_covered_standalone_table_references(
    change: SectionChange,
) -> SectionChange | None:
    """Hide context-free table renumbers only in reader-facing formats."""

    kept_pairs = [
        pair
        for pair in change.replaced_snippets
        if not _reader_standalone_table_reference_is_covered(
            pair,
            change.replaced_snippets,
        )
    ]
    cleaned = replace(change, replaced_snippets=kept_pairs)
    if (
        not cleaned.removed_snippets
        and not cleaned.added_snippets
        and not cleaned.replaced_snippets
        and cleaned.omitted_snippet_count == 0
    ):
        return None
    return cleaned


def _reader_standalone_table_reference_is_covered(
    pair: SnippetPair,
    pairs: list[SnippetPair],
) -> bool:
    """Return whether a longer sentence already explains one naked table reference."""

    old_text = pair.old
    new_text = pair.new
    old_reference = _reader_standalone_table_reference_number(old_text)
    new_reference = _reader_standalone_table_reference_number(new_text)
    if not old_reference or not new_reference:
        return False
    for other in pairs:
        if other is pair:
            continue
        other_old = other.old
        other_new = other.new
        if (
            _reader_text_contains_table_reference(other_old, old_reference)
            and _reader_text_contains_table_reference(other_new, new_reference)
            and len(compact_inline(other_old)) > len(compact_inline(old_text))
            and len(compact_inline(other_new)) > len(compact_inline(new_text))
        ):
            return True
    return False


_READER_STRICT_TABLE_REFERENCE_RE = re.compile(
    rf"(?i)\btable\s+(?P<number>\d+(?:\s*{TABLE_NUMBER_DASH_CLASS}\s*\d+)?)"
    rf"(?!\w|\s*{TABLE_NUMBER_DASH_CLASS}|\s*/|\s*\.(?=\S))"
)


def _reader_standalone_table_reference_number(value: str) -> str:
    """Return the normalized number only when the whole reader unit is `Table N`."""

    candidate = compact_inline(value)
    match = _READER_STRICT_TABLE_REFERENCE_RE.match(candidate)
    if match is None or not re.fullmatch(r"\s*[.:]?", candidate[match.end() :]):
        return ""
    return re.sub(
        rf"\s*{TABLE_NUMBER_DASH_CLASS}\s*",
        "-",
        match.group("number"),
    )


def _reader_text_contains_table_reference(value: str, number: str) -> bool:
    """Return whether a longer reader unit cites one exact table number."""

    references = {
        re.sub(
            rf"\s*{TABLE_NUMBER_DASH_CLASS}\s*",
            "-",
            match.group("number"),
        )
        for match in _READER_STRICT_TABLE_REFERENCE_RE.finditer(compact_inline(value))
    }
    return number in references


def _reader_change_without_evidenced_table_caption_fragments(
    change: SectionChange,
    table_evidence: (
        list[TableChange | _TableVisualGroup]
        | tuple[TableChange | _TableVisualGroup, ...]
    ),
) -> SectionChange | None:
    """Remove reader-only caption debris already represented by a same-page table."""

    old_tables = _reader_tables_for_change_side(change, table_evidence, side="old")
    new_tables = _reader_tables_for_change_side(change, table_evidence, side="new")
    removed = _reader_clean_single_side_table_fragments(change.removed_snippets, old_tables)
    added = _reader_clean_single_side_table_fragments(change.added_snippets, new_tables)
    replaced = [
        pair
        for pair in change.replaced_snippets
        if not _reader_replaced_table_reference_is_evidenced(
            pair,
            change,
            table_evidence,
        )
    ]
    cleaned = replace(
        change,
        removed_snippets=removed,
        added_snippets=added,
        replaced_snippets=replaced,
    )
    if (
        not cleaned.removed_snippets
        and not cleaned.added_snippets
        and not cleaned.replaced_snippets
        and cleaned.omitted_snippet_count == 0
    ):
        return None
    return cleaned


def _reader_replaced_table_reference_is_evidenced(
    pair: SnippetPair,
    change: SectionChange,
    table_evidence: (
        list[TableChange | _TableVisualGroup]
        | tuple[TableChange | _TableVisualGroup, ...]
    ),
) -> bool:
    """Hide a naked renumber already represented by one paired table card/group."""

    old_number = _reader_standalone_table_reference_number(pair.old)
    new_number = _reader_standalone_table_reference_number(pair.new)
    if (
        not old_number
        or not new_number
        or change.old_section is None
        or change.new_section is None
    ):
        return False
    for evidence in table_evidence:
        if not evidence.old_tables or not evidence.new_tables:
            continue
        if not all(
            change.old_section.start_page <= table.page_number <= change.old_section.end_page
            for table in evidence.old_tables
        ) or not all(
            change.new_section.start_page <= table.page_number <= change.new_section.end_page
            for table in evidence.new_tables
        ):
            continue
        old_numbers = {
            number
            for table in evidence.old_tables
            if (number := _reader_table_caption_number(table.title))
        }
        new_numbers = {
            number
            for table in evidence.new_tables
            if (number := _reader_table_caption_number(table.title))
        }
        if old_number in old_numbers and new_number in new_numbers:
            return True
    return False


def _reader_table_caption_number(value: str) -> str:
    """Return one strict normalized number from the start of a visual caption."""

    match = _READER_STRICT_TABLE_REFERENCE_RE.match(compact_inline(value))
    if match is None:
        return ""
    return re.sub(
        rf"\s*{TABLE_NUMBER_DASH_CLASS}\s*",
        "-",
        match.group("number"),
    )


def _reader_tables_for_change_side(
    change: SectionChange,
    table_evidence: (
        list[TableChange | _TableVisualGroup]
        | tuple[TableChange | _TableVisualGroup, ...]
    ),
    *,
    side: str,
) -> tuple[TableVisual, ...]:
    """Return only table visuals physically contained by this section side."""

    section = change.old_section if side == "old" else change.new_section
    if section is None:
        return ()
    tables = (
        table
        for evidence in table_evidence
        for table in (evidence.old_tables if side == "old" else evidence.new_tables)
    )
    return tuple(
        table
        for table in tables
        if section.start_page <= table.page_number <= section.end_page
    )


def _reader_clean_single_side_table_fragments(
    snippets: list[str],
    tables: tuple[TableVisual, ...],
) -> list[str]:
    """Strip a proven caption prefix and drop numbered naked-caption debris."""

    cleaned: list[str] = []
    for snippet in snippets:
        value = _reader_strip_evidenced_table_caption_prefix(snippet, tables)
        if _reader_is_numbered_table_caption_fragment(value, tables):
            continue
        cleaned.append(value)
    return cleaned


def _reader_strip_evidenced_table_caption_prefix(
    value: str,
    tables: tuple[TableVisual, ...],
) -> str:
    """Remove an exact visual caption only when readable prose follows it."""

    compact = compact_inline(value)
    for title in sorted(
        (compact_inline(table.title) for table in tables if compact_inline(table.title)),
        key=len,
        reverse=True,
    ):
        if not compact.casefold().startswith(title.casefold()):
            continue
        remainder = compact[len(title) :].lstrip(" :-")
        if (
            len(remainder) >= 20
            and _READER_PROSE_VERB_RE.search(remainder)
            and re.match(r"[A-Z\u3400-\u4dbf\u4e00-\u9fff]", remainder)
        ):
            return remainder
    return value


def _reader_is_numbered_table_caption_fragment(
    value: str,
    tables: tuple[TableVisual, ...],
) -> bool:
    """Recognize `9 10 Table 32-7.` only with a same-page visual identity."""

    match = re.fullmatch(
        rf"(?:\d{{1,3}}\s+){{1,6}}table\s+"
        rf"(?P<number>\d+(?:\s*{TABLE_NUMBER_DASH_CLASS}\s*\d+)?)\s*[.:]?",
        compact_inline(value),
        flags=re.I,
    )
    if match is None:
        return False
    number = normalize_table_number_dashes(match.group("number"))
    return any(_reader_table_title_number(table.title) == number for table in tables)


def _reader_table_title_number(value: str) -> str:
    """Return the strict normalized number of one visible table caption."""

    match = _NUMBERED_TABLE_CAPTION_RE.match(compact_inline(value))
    return normalize_table_number_dashes(match.group("number")) if match else ""


def _reader_change_is_coordinate_proven_table_body_duplicate(
    change: SectionChange,
    table_evidence: (
        list[TableChange | _TableVisualGroup]
        | tuple[TableChange | _TableVisualGroup, ...]
    ),
) -> bool:
    """Hide only a losslessly structured table repeated as layout-order text.

    This is deliberately a reader-layer decision.  It requires complete bbox
    character coverage on both versions, exact paired captions, section/page
    containment, and either bidirectional token coverage or an exact aggregate
    character proof. Logical row alignment stays separately auditable and is
    not needed to prove that the same raw table text is already preserved by its
    table visual. The unfiltered SectionChange stays in JSON/CSV for audit.
    """

    if not (
        change.change_type == "modified"
        and change.old_section is not None
        and change.new_section is not None
        and change.replaced_snippets
        and not change.added_snippets
        and not change.removed_snippets
        and change.omitted_snippet_count == 0
    ):
        return False
    pairwise_layout_proven = all(
        _reader_snippet_collapse_kind(pair.old) == "layout"
        and _reader_snippet_collapse_kind(pair.new) == "layout"
        and compact_inline(_reader_visible_prose_tail(pair.old))
        == compact_inline(_reader_visible_prose_tail(pair.new))
        for pair in change.replaced_snippets
    )
    cross_pair_table_fragment = _reader_single_cross_pair_table_fragment(
        change.replaced_snippets,
    )
    if not pairwise_layout_proven and cross_pair_table_fragment is None:
        return False
    old_snippet_text = " ".join(pair.old for pair in change.replaced_snippets)
    new_snippet_text = " ".join(pair.new for pair in change.replaced_snippets)
    eligible_evidence: list[
        tuple[TableChange | _TableVisualGroup, str, str]
    ] = []
    for table_change in table_evidence:
        if not _reader_table_change_proves_section_duplicate(
            change,
            table_change,
        ):
            continue
        old_table_text = _reader_table_group_audit_text(table_change.old_tables)
        new_table_text = _reader_table_group_audit_text(table_change.new_tables)
        eligible_evidence.append((table_change, old_table_text, new_table_text))
        if (
            pairwise_layout_proven
            and _reader_table_token_coverage_proves_duplicate(
                old_snippet_text,
                old_table_text,
            )
            and _reader_table_token_coverage_proves_duplicate(
                new_snippet_text,
                new_table_text,
            )
            and _reader_uncovered_evidence_tokens(
                old_snippet_text,
                old_table_text,
            )
            == _reader_uncovered_evidence_tokens(
                new_snippet_text,
                new_table_text,
            )
        ):
            return True
    relevant_evidence = [
        (old_table_text, new_table_text)
        for _table_change, old_table_text, new_table_text in eligible_evidence
        if _reader_table_text_contributes_to_snippet(old_snippet_text, old_table_text)
        and _reader_table_text_contributes_to_snippet(new_snippet_text, new_table_text)
    ]
    if (
        eligible_evidence
        and _reader_nonspace_character_counts(old_snippet_text)
        == _reader_nonspace_character_counts(new_snippet_text)
        and any(
            _reader_table_character_coverage_contributes(
                old_snippet_text,
                old_table_text,
            )
            and _reader_table_character_coverage_contributes(
                new_snippet_text,
                new_table_text,
            )
            and (
                pairwise_layout_proven
                or (
                    cross_pair_table_fragment is not None
                    and isinstance(table_change, TableChange)
                    and bool(_table_review_rows(table_change))
                    and _reader_table_text_covers_cross_pair_fragment(
                        cross_pair_table_fragment,
                        old_table_text=old_table_text,
                        new_table_text=new_table_text,
                    )
                )
            )
            for table_change, old_table_text, new_table_text in eligible_evidence
        )
    ):
        return True  # 精确保留大小写、数字形态、PUA、希腊字母与运算符；只忽略版面空白和字符顺序。
    if not pairwise_layout_proven:
        return False  # 跨 pair 可读句迁移只开放上面的精确字符守恒路径，不能进入较宽松 token 合并。
    if len(relevant_evidence) < 2:
        return False
    combined_old_text = " ".join(old_text for old_text, _new_text in relevant_evidence)
    combined_new_text = " ".join(new_text for _old_text, new_text in relevant_evidence)
    return bool(
        _reader_table_token_coverage_proves_duplicate(
            old_snippet_text,
            combined_old_text,
        )
        and _reader_table_token_coverage_proves_duplicate(
            new_snippet_text,
            combined_new_text,
        )
        and _reader_uncovered_evidence_tokens(
            old_snippet_text,
            combined_old_text,
        )
        == _reader_uncovered_evidence_tokens(
            new_snippet_text,
            combined_new_text,
        )
    )


def _reader_single_cross_pair_table_fragment(
    pairs: list[SnippetPair],
) -> tuple[str, str] | None:
    """Return one side-specific table fragment moved across replacement pairs.

    The shorter unit must survive byte-for-byte (after whitespace compaction) as
    a complete prefix or suffix of the longer unit.  Only the extra fragment is
    returned; coordinate table evidence validates it separately.
    """

    transferred: tuple[str, str] | None = None
    for pair in pairs:
        old_kind = _reader_snippet_collapse_kind(pair.old)
        new_kind = _reader_snippet_collapse_kind(pair.new)
        old_tail = compact_inline(_reader_visible_prose_tail(pair.old))
        new_tail = compact_inline(_reader_visible_prose_tail(pair.new))
        if (
            old_kind == "layout"
            and new_kind == "layout"
            and old_tail == new_tail
        ):
            continue
        if transferred is not None:
            return None
        old_text = compact_inline(pair.old)
        new_text = compact_inline(pair.new)
        if not old_text or not new_text:
            return None
        if len(new_text) >= len(old_text) + 80 and (
            new_text.startswith(old_text) or new_text.endswith(old_text)
        ):
            extra = (
                new_text[len(old_text) :]
                if new_text.startswith(old_text)
                else new_text[: -len(old_text)]
            ).strip()
            transferred = ("", extra)
        elif len(old_text) >= len(new_text) + 80 and (
            old_text.startswith(new_text) or old_text.endswith(new_text)
        ):
            extra = (
                old_text[len(new_text) :]
                if old_text.startswith(new_text)
                else old_text[: -len(new_text)]
            ).strip()
            transferred = (extra, "")
        else:
            return None
        old_extra, new_extra = transferred
        extra = old_extra or new_extra
        if not extra or _READER_PROSE_VERB_RE.search(extra):
            return None
    # 普通 prose 字序修改、含规范动词的附加段、短前缀和多处迁移均保留。
    return transferred


def _reader_table_text_covers_cross_pair_fragment(
    fragment: tuple[str, str],
    *,
    old_table_text: str,
    new_table_text: str,
) -> bool:
    """Require every nonspace fragment character in its same-side table audit."""

    old_fragment, new_fragment = fragment
    for value, table_text in (
        (old_fragment, old_table_text),
        (new_fragment, new_table_text),
    ):
        if not value:
            continue
        value_counts = _reader_nonspace_character_counts(value)
        table_counts = _reader_nonspace_character_counts(table_text)
        if not value_counts or value_counts - table_counts:
            return False
    return True


def _reader_nonspace_character_counts(value: str) -> Counter[str]:
    """Return a case- and glyph-sensitive character multiset, ignoring whitespace only."""

    return Counter(character for character in value if not character.isspace())


def _reader_table_character_coverage_contributes(
    snippet_text: str,
    table_text: str,
) -> bool:
    """Require near-total snippet coverage and substantial table coverage by exact chars."""

    snippet_counts = _reader_nonspace_character_counts(snippet_text)
    table_counts = _reader_nonspace_character_counts(table_text)
    snippet_total = sum(snippet_counts.values())
    table_total = sum(table_counts.values())
    if snippet_total < 160 or table_total < 160:
        return False
    shared_count = sum((snippet_counts & table_counts).values())
    return bool(
        shared_count / snippet_total >= 0.95
        and shared_count / table_total >= 0.35
    )


def _reader_table_change_proves_section_duplicate(
    change: SectionChange,
    table_change: TableChange | _TableVisualGroup,
) -> bool:
    """Bind one duplicate candidate to the same named table and page ranges."""

    if not table_change.old_tables or not table_change.new_tables:
        return False
    tables = (*table_change.old_tables, *table_change.new_tables)
    if not all(table.content_fully_represented for table in tables):
        return False
    if not all(table.row_alignment_reliable for table in tables):
        if not isinstance(table_change, TableChange) or not _table_review_rows(table_change):
            return False  # 行归属未知时只有显式、可见的复核卡才能替代重复正文墙；静默组不得授权隐藏。
    old_caption_keys = {
        key
        for table in table_change.old_tables
        if (key := _table_visual_caption_key(table))
    }
    new_caption_keys = {
        key
        for table in table_change.new_tables
        if (key := _table_visual_caption_key(table))
    }
    if not old_caption_keys or old_caption_keys != new_caption_keys:
        return False
    assert change.old_section is not None and change.new_section is not None
    return (
        all(
            change.old_section.start_page <= table.page_number <= change.old_section.end_page
            for table in table_change.old_tables
        )
        and all(
            change.new_section.start_page <= table.page_number <= change.new_section.end_page
            for table in table_change.new_tables
        )
    )


def _reader_table_group_audit_text(tables: tuple[TableVisual, ...]) -> str:
    """Serialize caption and rows only for conservative duplicate coverage."""

    return " ".join(
        part
        for table in tables
        for part in (table.title, *table.row_texts)
        if compact_inline(part)
    )


def _reader_table_body_tokens(value: str) -> list[str]:
    """Tokenize every visible token without erasing case, `%`, or number shape."""

    return [match.group(0) for match in _INLINE_TOKEN_RE.finditer(value)]


def _reader_table_text_contributes_to_snippet(
    snippet_text: str,
    table_text: str,
) -> bool:
    """Require one table to contribute a meaningful token share before combining it."""

    snippet_tokens = _reader_table_body_tokens(snippet_text)
    table_tokens = _reader_table_body_tokens(table_text)
    if len(table_tokens) < 8:
        return False
    shared_count = sum(
        (Counter(snippet_tokens) & Counter(table_tokens)).values()
    )
    return shared_count >= 8 and shared_count / len(table_tokens) >= 0.35


def _reader_uncovered_evidence_tokens(
    snippet_text: str,
    table_text: str,
) -> list[str]:
    """Return snippet tokens not consumed by the paired table's exact multiset."""

    available = Counter(_reader_table_body_tokens(table_text))
    residual: list[str] = []
    for token in _reader_table_body_tokens(snippet_text):
        if available[token] > 0:
            available[token] -= 1
        else:
            residual.append(token)
    return residual


def _reader_table_token_coverage_proves_duplicate(
    snippet_text: str,
    table_text: str,
) -> bool:
    """Require substantial multiset coverage in both directions."""

    snippet_tokens = _reader_table_body_tokens(snippet_text)
    table_tokens = _reader_table_body_tokens(table_text)
    if len(snippet_tokens) < 80 or len(table_tokens) < 80:
        return False
    snippet_counts = Counter(snippet_tokens)
    table_counts = Counter(table_tokens)
    shared_count = sum((snippet_counts & table_counts).values())
    return bool(
        shared_count / len(snippet_tokens) >= 0.65
        and shared_count / len(table_tokens) >= 0.35
    )


def _reader_change_is_layout_reorder_only(change: SectionChange) -> bool:
    """Recognize a section whose every visible delta is one layout token reorder."""

    return bool(
        change.change_type == "modified"
        and change.replaced_snippets
        and not change.added_snippets
        and not change.removed_snippets
        and change.omitted_snippet_count == 0
        and all(
            _reader_pair_is_layout_token_reorder(pair.old, pair.new)
            for pair in change.replaced_snippets
        )
    )


def _reader_pair_is_layout_token_reorder(old_text: str, new_text: str) -> bool:
    """Return True only when reordering is confined to explicit header fields."""

    if (
        _reader_snippet_collapse_kind(old_text) != "layout"
        or _reader_snippet_collapse_kind(new_text) != "layout"
        or not _reader_reorder_has_table_schema(old_text)
        or not _reader_reorder_has_table_schema(new_text)
        or compact_inline(_reader_visible_prose_tail(old_text))
        != compact_inline(_reader_visible_prose_tail(new_text))
    ):
        return False
    old_tokens = [token.text for token in _inline_tokens(old_text)]
    new_tokens = [token.text for token in _inline_tokens(new_text)]
    if (
        len(old_tokens) < 8
        or old_tokens == new_tokens
        or Counter(old_tokens) != Counter(new_tokens)
    ):
        return False
    prefix_length = 0
    while (
        prefix_length < min(len(old_tokens), len(new_tokens))
        and old_tokens[prefix_length] == new_tokens[prefix_length]
    ):
        prefix_length += 1
    suffix_length = 0
    while (
        suffix_length < min(len(old_tokens), len(new_tokens)) - prefix_length
        and old_tokens[-(suffix_length + 1)] == new_tokens[-(suffix_length + 1)]
    ):
        suffix_length += 1
    old_end = len(old_tokens) - suffix_length if suffix_length else len(old_tokens)
    new_end = len(new_tokens) - suffix_length if suffix_length else len(new_tokens)
    reordered_old = old_tokens[prefix_length:old_end]
    reordered_new = new_tokens[prefix_length:new_end]
    return bool(
        reordered_old
        and reordered_new
        and all(_reader_token_is_explicit_table_header(token) for token in reordered_old)
        and all(_reader_token_is_explicit_table_header(token) for token in reordered_new)
    )


def _reader_token_is_explicit_table_header(token: str) -> bool:
    """Accept one atomic token only when it is itself a known table field."""

    match = _READER_LAYOUT_HEADER_RE.fullmatch(token)
    return match is not None


def _reader_reorder_has_table_schema(value: str) -> bool:
    """Require several explicit table fields before calling a reorder layout-only."""

    fields = {
        match.group(0).casefold()
        for match in _READER_LAYOUT_HEADER_RE.finditer(value)
    }
    return len(fields) >= 6


def _reader_difference_technical_priority(
    old_tokens: list[_InlineToken],
    new_tokens: list[_InlineToken],
    *,
    old_context: list[_InlineToken],
    new_context: list[_InlineToken],
) -> int:
    """Rank numeric limits, units, operators, and protocol identifiers first."""

    changed_text = " ".join(token.text for token in [*old_tokens, *new_tokens])
    context_text = " ".join(
        token.text for token in [*old_context, *new_context]
    )
    if re.search(
        r"(?i)<=|>=|≤|≥|≠|\b(?:ui|db|[kmg]hz|mv|ma|ohm|ppm|table|figure|section|"
        r"limit|threshold|minimum|maximum|min|max|shall|must|requirement)\b",
        context_text,
    ):
        return 0
    if re.search(r"\d", changed_text):
        return 1
    return 2


def _reader_anchor_tokens_with_attached_prefix(
    source: str,
    tokens: list[_InlineToken],
    start: int,
    end: int,
) -> list[_InlineToken]:
    """Keep the unchanged ``31`` beside a changed ``-9`` in equation labels."""

    selected = tokens[start:end]
    if (
        selected
        and start > 0
        and selected[0].text.startswith("-")
        and source[tokens[start - 1].end : selected[0].start] == ""
    ):
        return tokens[start - 1 : end]
    return selected


def _reader_difference_anchor(
    source: str,
    tokens: list[_InlineToken],
    *,
    max_chars: int = 48,
) -> str:
    """Return one bounded, printable source span for a folded-pair difference."""

    if not tokens:
        return "（无）"
    anchor = compact_inline(source[tokens[0].start : tokens[-1].end])
    anchor = re.sub(r"[\ue000-\uf8ff]+", "[未映射符号]", anchor)
    if len(anchor) <= max_chars:
        return anchor
    return anchor[: max_chars - 1].rstrip() + "…"


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

    old_text = readable_symbol_font_glyphs(old_text)
    new_text = readable_symbol_font_glyphs(new_text)
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


def _inline_tokens(text: str, *, field_label: str = "") -> list[_InlineToken]:
    """Tokenize visible text and compute noise-tolerant keys for highlighting."""

    raw_tokens: list[tuple[str, int, int]] = []
    for match in _INLINE_TOKEN_RE.finditer(text):
        if _is_arrow_operator_noise(text, match.start(), match.end()):
            continue
        raw_tokens.extend(
            _expand_chinese_inline_token(
                text[match.start() : match.end()],
                match.start(),
                text,
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
            phrase_end = raw_tokens[index + consumed - 1][2]
            if (
                _number_word_phrase_has_positive_count_context(
                    raw_tokens,
                    raw_words,
                    index,
                    consumed,
                )
                and not _span_is_inside_paired_literal(text, start, phrase_end)
            ):
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
        key = _inline_token_key(
            raw,
            text,
            start,
            end,
            field_label=field_label,
        )
        if key:
            tokens.append(_InlineToken(text=raw, start=start, end=end, key=key))
        index += 1
    return tokens


def _number_word_phrase_has_positive_count_context(
    raw_tokens: list[tuple[str, int, int]],
    raw_words: list[str],
    index: int,
    consumed: int,
) -> bool:
    """Fold an English number word only when a following count noun proves it."""

    next_index = index + consumed
    if next_index >= len(raw_tokens):
        return False
    first = raw_words[next_index]
    if _looks_like_plural_count_noun(first):
        return True
    second_index = next_index + 1
    if (
        _looks_like_english_noun_modifier(first)
        and second_index < len(raw_words)
        and _looks_like_plural_count_noun(raw_words[second_index])
    ):
        return True  # `twenty one idle intervals`: 允许一个形容/复合修饰词。

    # 工程文档也常见 `seven 2.0 million unit-interval waveforms`：
    # 数值+量级+一个修饰词+复数名词仍是完整的正向计数证据。
    scale_index = next_index + 1
    modifier_index = next_index + 2
    noun_index = next_index + 3
    return bool(
        _INLINE_NUMBER_RE.fullmatch(first)
        and noun_index < len(raw_words)
        and raw_words[scale_index] in {
            "hundred",
            "thousand",
            "million",
            "billion",
            "trillion",
        }
        and _looks_like_english_noun_modifier(raw_words[modifier_index])
        and _looks_like_plural_count_noun(raw_words[noun_index])
    )


def _looks_like_english_noun_modifier(token: str) -> bool:
    """Return True for one uninterrupted word/compound inside a count phrase."""

    return bool(re.fullmatch(r"[a-z]+(?:-[a-z]+)*", token))


def _looks_like_plural_count_noun(token: str) -> bool:
    """Recognize generic plural count nouns without a document vocabulary list."""

    if not _looks_like_english_noun_modifier(token):
        return False
    terminal = token.rsplit("-", 1)[-1]
    if terminal in {
        "children",
        "feet",
        "indices",
        "matrices",
        "men",
        "people",
        "teeth",
        "women",
    }:
        return True
    if terminal in {
        "as",
        "does",
        "has",
        "his",
        "is",
        "less",
        "minus",
        "plus",
        "status",
        "this",
        "was",
        "yes",
    }:
        return False
    return len(terminal) > 2 and terminal.endswith("s") and not terminal.endswith("ss")


def _span_is_inside_paired_literal(text: str, start: int, end: int) -> bool:
    """Return True when a span is enclosed by a complete quote pair."""

    for opening, closing in (("'", "'"), ('"', '"'), ("`", "`"), ("‘", "’"), ("“", "”")):
        cursor = 0
        while cursor < len(text):
            opening_index = text.find(opening, cursor)
            if opening_index < 0:
                break
            closing_index = text.find(closing, opening_index + len(opening))
            if closing_index < 0:
                break
            if opening_index < start and end <= closing_index:
                return True
            cursor = closing_index + len(closing)
    return False


def _is_arrow_operator_noise(text: str, start: int, end: int) -> bool:
    """Return True when ``<``/``>`` is part of an extracted arrow, not a limit."""

    token = text[start:end]
    if token not in {"<", ">"}:
        return False
    left = text[max(0, start - 3) : start]
    right = text[end : min(len(text), end + 3)]
    return bool(re.search(r"-\s*$", left) or re.match(r"^\s*-", right))


def _expand_chinese_inline_token(
    raw: str,
    start: int,
    source_text: str,
) -> list[tuple[str, int, int]]:
    """Split Chinese count numerals out of a larger Chinese token.

    PDF text extraction often returns ``供应商应捕获七个波形`` as one token while
    ``供应商应捕获 7 个波形`` becomes three tokens. Splitting only clear numeric
    spans lets the highlighter align ``七`` and ``7`` without over-tokenizing all
    Chinese prose.
    """

    if not raw or not re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff]+", raw):
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
    next_character = source_text[start + len(raw) : start + len(raw) + 1]
    if not expanded and next_character and re.match(r"[^\W\d_\u3400-\u4dbf\u4e00-\u9fff]", next_character):
        trailing_number = re.search(rf"[{CHINESE_NUMBER_CHARS}]+$", raw)
        if trailing_number and canonicalize_chinese_number_token(trailing_number.group(0)) is not None:
            if trailing_number.start() > 0:
                expanded.append((raw[: trailing_number.start()], start, start + trailing_number.start()))
            expanded.append(
                (
                    trailing_number.group(0),
                    start + trailing_number.start(),
                    start + trailing_number.end(),
                )
            )
            cursor = trailing_number.end()
    if not expanded:
        return [(raw, start, start + len(raw))]
    if cursor < len(raw):
        expanded.append((raw[cursor:], start + cursor, start + len(raw)))
    return expanded


def _inline_token_key(
    token: str,
    source_text: str,
    start: int,
    end: int,
    *,
    field_label: str = "",
) -> str:
    """Normalize one token for display highlighting, not comparison semantics."""

    if _XML_TAG_RE.fullmatch(token):
        return f"xml-tag:{token}"
    if _VARIABLE_LITERAL_RE.fullmatch(token):
        return f"variable-literal:{token}"
    if _SLASH_LITERAL_RE.fullmatch(token):
        return f"slash-literal:{token}"
    if _BACKSLASH_PATH_RE.fullmatch(token):
        return f"backslash-path:{token}"
    if _CLI_OPTION_RE.fullmatch(token):
        return f"cli-option:{token}"
    if _ABSOLUTE_PATH_RE.fullmatch(token):
        return f"absolute-path:{token}"
    if _HEX_LITERAL_RE.fullmatch(token):
        return f"hex-literal:{token.casefold()}"
    if _DOTTED_IDENTIFIER_RE.fullmatch(token):
        return f"dotted-identifier:{token}"
    micro_signatures = micro_identifier_signatures(
        token,
        context=f"{source_text[:start]} {field_label}",
    )
    if micro_signatures:
        return " | ".join(f"micro-identifier:{item}" for item in micro_signatures)
    if token in _DIRECTIONAL_SYMBOL_KEYS:
        if token == "<-" and not _ascii_left_arrow_has_explicit_context(source_text, start, end):
            return "relation:less-than-negative"
        return _DIRECTIONAL_SYMBOL_KEYS[token]
    if token.casefold() == "x" and _inline_x_is_multiply(source_text, start, end):
        return "operator:multiply"
    if _is_unambiguous_inline_list_marker(token, source_text, start, end):
        return ""
    if token in {"-", "*"} and _is_leading_ascii_symbol(token, source_text, start, end):
        return f"literal:{token}"
    if token in {"'", '"'}:
        modifier_key = _inline_measurement_modifier_key(token, source_text, start, end)
        if modifier_key:
            return f"modifier:{modifier_key}"
    if token in {"'", '"', "`", "‘", "’", "“", "”"}:
        quote_key = _inline_literal_quote_key(token, source_text, start, end)
        return f"quote:{quote_key}" if quote_key else ""
    if re.fullmatch(r"[.,;:?]+", token):
        punctuation_key = _inline_contextual_punctuation_key(token, source_text, start, end)
        return f"punctuation:{punctuation_key}" if punctuation_key else ""
    if token in _SEMANTIC_OPERATOR_KEYS:
        operator_key = _inline_semantic_operator_key(token, source_text, start, end)
        return f"operator:{operator_key}" if operator_key else ""
    if token == "&" and _inline_ampersand_is_semantic(
        source_text,
        start,
        field_label=field_label,
    ):
        return "operator:ampersand"
    case_source = token.replace("µ", "u").replace("μ", "u")
    case_source = case_source.replace("&", "and")
    case_source = case_source.replace("≤", "<=").replace("≥", ">=").replace("≠", "!=")
    case_source = _normalize_inline_math_token(case_source, source_text, start)
    normalized = _inline_context_word(case_source)
    if re.fullmatch(r"e[+-]?\d+", normalized):
        return _canonical_inline_number(f"1{normalized}")  # 裸 `E-12` 与 `1e-12` 都表示同一指数值。
    chinese_number = _contextual_chinese_number_key(normalized, source_text, start, end)
    if chinese_number is not None:
        return chinese_number
    if _INLINE_NUMBER_RE.fullmatch(normalized):
        numeric_key = _canonical_inline_number(normalized)
        lexical_signature = _numeric_lexical_signature(
            token,
            source_text,
            start,
            field_label=field_label,
        )
        return (
            f"{numeric_key} | lexical-number:{lexical_signature}"
            if lexical_signature
            else numeric_key
        )
    case_signatures = _technical_case_signatures(source_text, target_span=(start, end))
    if (
        not case_signatures
        and bool(compact_inline(field_label))
        and compact_inline(source_text) == token
        and any(
            character.isupper()
            for character in token
            if character.lower() != character.upper()
        )
    ):
        case_signatures = [token]
    return " | ".join(
        [normalized, *(f"case:{signature}" for signature in case_signatures)]
    )


def _inline_ampersand_is_semantic(
    source_text: str,
    start: int,
    *,
    field_label: str = "",
) -> bool:
    """Keep ``&`` when assignment/formula evidence makes it an operator."""

    line_start = source_text.rfind("\n", 0, start) + 1
    if "=" in source_text[line_start:start]:
        return True
    normalized_label = re.sub(r"[^a-z]+", " ", field_label.casefold()).strip()
    return bool(
        re.search(
            r"\b(?:bitmask|bitmap|condition|equation|expression|flags?|formula|logic|mask|predicate)\b",
            normalized_label,
        )
    )


def _ascii_left_arrow_has_explicit_context(source_text: str, start: int, end: int) -> bool:
    """Reject compact ``<-`` when it can mean less-than followed by a negative."""

    return bool(
        start > 0
        and end < len(source_text)
        and source_text[start - 1].isspace()
        and source_text[end].isspace()
    )


def _inline_x_is_multiply(source_text: str, start: int, end: int) -> bool:
    """Recognize a letter x as multiplication only in an explicit numeric position."""

    return bool(
        re.search(r"\d\s*$", source_text[:start])
        and re.match(r"^\s*(?:\d|[^\W\d_]|_)", source_text[end:])
    )


def _is_unambiguous_inline_list_marker(
    token: str,
    source_text: str,
    start: int,
    end: int,
) -> bool:
    """Ignore only position-proven Unicode bullets and numeric delimiters."""

    before = source_text[:start]
    after = source_text[end:]
    if token in _UNICODE_BULLET_MARKERS:
        return not before.strip() and bool(re.match(r"^\s+", after))
    if token == ")" and re.fullmatch(r"\s*\d{1,3}", before):
        return bool(re.match(r"^\s+", after))
    return False


def _is_leading_ascii_symbol(
    token: str,
    source_text: str,
    start: int,
    end: int,
) -> bool:
    """Keep ambiguous leading ASCII symbols as observable content."""

    return (
        token in {"-", "*"}
        and not source_text[:start].strip()
        and bool(re.match(r"^\s+", source_text[end:]))
    )


def _inline_measurement_modifier_key(
    token: str,
    source_text: str,
    start: int,
    end: int,
) -> str | None:
    """Classify an ASCII quote only when its visible neighbors make it technical."""

    before = source_text[:start]
    if token == '"' and re.search(r"\d\s*$", before):
        return "inch"
    if (
        token == '"'
        and re.search(r"[^\W\d_]$", before, flags=re.UNICODE)
        and not re.match(r"^\w", source_text[end:])
    ):
        return "double-prime"
    if token == "'" and re.search(r"\d\s*$", before):
        return "foot"
    if (
        token == "'"
        and re.search(r"[^\W\d_]$", before, flags=re.UNICODE)
        and not re.match(r"^\w", source_text[end:])
    ):
        return "prime"
    return None


def _inline_literal_quote_key(
    token: str,
    source_text: str,
    start: int,
    end: int,
) -> str | None:
    """Bind paired quotes to literal content, including multi-word/slash values."""

    quote_pairs = {"‘": "’", "“": "”", "'": "'", '"': '"', "`": "`"}
    if token in quote_pairs:
        closing = quote_pairs[token]
        closing_index = source_text.find(closing, end)
        if closing_index >= 0 and source_text[end:closing_index].strip():
            return "open"
    opening_by_close = {closing: opening for opening, closing in quote_pairs.items()}
    opening = opening_by_close.get(token)
    if opening:
        opening_index = source_text.rfind(opening, 0, start)
        if opening_index >= 0 and source_text[opening_index + len(opening) : start].strip():
            return "close"
    return None


def _inline_contextual_punctuation_key(
    token: str,
    source_text: str,
    start: int,
    end: int,
) -> str | None:
    """Preserve punctuation only when its neighbors show technical structure."""

    before = source_text[:start]
    after = source_text[end:]
    if len(token) > 1:
        punctuation_names = {
            ".": "period",
            ",": "comma",
            ";": "semicolon",
            ":": "colon",
            "?": "question",
        }
        run_shape = "+".join(punctuation_names[character] for character in token)
        if re.search(r"\w\s*$", before) or re.match(r"^\s*\w", after):
            return f"run:{run_shape}"  # 连续标点的类型、数量和出现位置都是可观测事实。
        return None
    if token == ".":
        if re.search(r"\w$", before) and re.match(r"^\w", after):
            return "member"
        return None
    if token == ",":
        if re.search(r"\w$", before) and re.match(r"^\s*\w", after):
            return "separator"
        return None
    if token == ";":
        if re.search(r"\w$", before) and re.match(r"^\s*\w", after):
            return "separator"
        return None
    if token == ":":
        if re.search(r"\w\s*$", before) and re.match(r"^\s*\w", after):
            return "colon"
        return None
    if token == "?":
        identifier_match = re.search(r"([^\W\d_]|_)\w*$", before)
        if identifier_match and _token_shape_is_technical(identifier_match.group(0)):
            return "query"
        if re.search(r"(?i)\b(?:command|query|function)\s+\w+\s*$", before):
            return "query"
    return None


def _token_shape_is_technical(token: str) -> bool:
    """Return True for identifier shapes that are unlikely to be prose words."""

    letters = [character for character in token if character.isalpha()]
    return bool(
        "_" in token
        or any(character.isdigit() for character in token)
        or any(character.isupper() for character in token[1:])
        or (len(letters) >= 2 and all(character.isupper() for character in letters))
    )


def _numeric_lexical_signature(
    token: str,
    source_text: str,
    start: int,
    *,
    field_label: str = "",
) -> str:
    """Keep identifier-like numeric spelling attached to its exact occurrence."""

    lexical = token.casefold().replace("−", "-")
    normalized = lexical.replace(",", "")
    unsigned = normalized.lstrip("+-")
    mantissa = unsigned.split("e", 1)[0]
    integer = mantissa.split(".", 1)[0]
    if len(integer) > 1 and integer.startswith("0"):
        return lexical
    previous_word_match = re.search(
        r"((?:[^\W\d_]|_)\w*)\s*(?:[:=#]\s*)?$",
        source_text[:start],
    )
    previous_word = previous_word_match.group(1).casefold() if previous_word_match else ""
    field_words = set(re.findall(r"(?:[^\W\d_]|_)\w*", field_label.casefold()))
    identifier_context = (
        previous_word in _IDENTIFIER_NUMBER_LABELS
        or bool(field_words & _IDENTIFIER_NUMBER_LABELS)
    )
    following_character = source_text[start + len(token) : start + len(token) + 1]
    boundary_index = start + len(token)
    if following_character and has_observable_identifier_boundary(
        source_text,
        boundary_index,
        context=field_label,
    ):
        return f"{lexical}:joined-to-identifier"
    if identifier_context and re.match(r"(?:[^\W\d_]|_)", following_character):
        return f"{lexical}:joined-to-identifier"
    if field_label and lexical != _canonical_inline_number(lexical):
        return lexical  # 任意结构化表头都是数字词形的正向语义证据。
    if (
        previous_word in _IDENTIFIER_NUMBER_LABELS
        or bool(field_words & _IDENTIFIER_NUMBER_LABELS)
    ) and lexical != _canonical_inline_number(lexical):
        return lexical
    return ""


def _inline_semantic_operator_key(
    token: str,
    source_text: str,
    start: int,
    end: int,
) -> str | None:
    """Return an operator key only with explicit formula-style neighbors."""

    if token in {"\u2061", "\u2062", "\u2063", "\u2064"}:
        return _SEMANTIC_OPERATOR_KEYS[token]
    left = source_text[:start]
    right = source_text[end:]
    if not re.search(r"[\w)\]]\s*$", left):
        return None
    if not re.match(r"^\s*[\w(\[]", right):
        return None
    if token == "-" and not (
        start > 0
        and end < len(source_text)
        and source_text[start - 1].isspace()
        and source_text[end].isspace()
    ):
        return None  # 无空格 ASCII 连字符更可能属于标识符或复合词。
    return _SEMANTIC_OPERATOR_KEYS[token]


def _normalize_inline_math_token(token: str, source_text: str, start: int) -> str:
    """Normalize visual-only multiplication/exponent token variants."""

    normalized = token.replace("−", "-").replace("–", "-")  # 数学负号和短横线都归一为 ASCII hyphen。
    scientific_match = re.fullmatch(
        r"(?i)([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*(?:x|×|\*)\s*10\s*"
        r"\^\s*([+-]?\d+)",
        normalized,
    )
    if scientific_match:
        return f"{scientific_match.group(1)}e{scientific_match.group(2)}"
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

    sign = token[0] if token.startswith(("+", "-")) else ""
    body = token.lstrip("+-")
    if body.startswith("."):
        body = f"0{body}"
    try:
        value = Decimal(body.replace(",", ""))
    except InvalidOperation:
        return token
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

    return html_lib.escape(reader_safe_glyphs(str(value)), quote=True)


def _rows_for_csv(changes: list[SectionChange]) -> list[dict[str, str]]:
    """Flatten section changes for spreadsheet review."""

    rows: list[dict[str, str]] = []
    for change in changes:
        audit_added = _audit_added_snippets(change)
        audit_removed = _audit_removed_snippets(change)
        audit_replaced = _audit_replaced_snippets(change)
        replaced = [
            f"旧: {pair.old} / 新: {pair.new}" for pair in audit_replaced
        ]
        summary_parts = []
        if audit_replaced:
            summary_parts.append(f"{len(audit_replaced)} 处替换")
        if audit_added:
            summary_parts.append(f"{len(audit_added)} 处新增")
        if audit_removed:
            summary_parts.append(f"{len(audit_removed)} 处删除")
        if change.omitted_snippet_count:
            summary_parts.append(f"读者报告有 {change.omitted_snippet_count} 处未展开")
        natures = _change_natures(change)
        if natures:
            summary_parts.append("重点关注: " + "、".join(natures))
        rows.append(
            {
                "change_type": _CHANGE_LABELS.get(change.change_type, change.change_type),
                "role": change.role,
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
                "match_basis": change.match_basis,
                "match_basis_label": _MATCH_BASIS_LABELS.get(
                    change.match_basis,
                    change.match_basis,
                ),
                "summary": "；".join(summary_parts),
                "added_snippets": "\n".join(audit_added),
                "removed_snippets": "\n".join(audit_removed),
                "replaced_snippets": "\n".join(replaced),
                "omitted_snippet_count": str(change.omitted_snippet_count),
            }
        )
    return rows


def _rows_for_table_csv(changes: list[TableChange]) -> list[dict[str, str]]:
    """Flatten the shared table-change model for spreadsheet review."""

    rows: list[dict[str, str]] = []
    for change in changes:
        fallback_kind = "表题/表号变化" if change.caption_changed else "未抽取到可靠行级文字"
        row_changes = change.row_changes or (TableRowChange("", "", "", fallback_kind),)
        for row_change in row_changes:
            rows.append(
                {
                    "table_change_type": _CHANGE_LABELS.get(change.change_type, change.change_type),
                    "role": change.role,
                    "old_titles": _table_titles(change.old_tables),
                    "new_titles": _table_titles(change.new_tables),
                    "old_pages": _table_pages(change.old_tables),
                    "new_pages": _table_pages(change.new_tables),
                    "pair_similarity": f"{change.similarity:.3f}" if change.old_tables and change.new_tables else "",
                    "item": row_change.item,
                    "old_value": row_change.old_value,
                    "new_value": row_change.new_value,
                    "row_change_type": row_change.change_type,
                }
            )
    return rows


def _table_titles(tables: tuple[TableVisual, ...]) -> str:
    """Return unique table captions in source order."""

    return " / ".join(_unique_table_titles(tables))


def _unique_table_titles(tables: tuple[TableVisual, ...]) -> list[str]:
    """Return de-duplicated table captions shared by HTML, JSON, and CSV."""

    return list(dict.fromkeys(compact_inline(table.title) for table in tables if table.title))


def _table_pages(tables: tuple[TableVisual, ...]) -> str:
    """Return comma-separated source PDF pages for a logical table."""

    return ",".join(str(table.page_number) for table in tables)


def _change_counts(changes: list[SectionChange]) -> dict[str, int]:
    """Count changes by type."""

    counts: dict[str, int] = {}
    for change in changes:
        counts[change.change_type] = counts.get(change.change_type, 0) + 1
    return counts


def _ordered_table_changes(changes: list[TableChange]) -> list[TableChange]:
    """Show technical table evidence before publication-history tables."""

    return sorted(changes, key=lambda change: change.role == "document_metadata")


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

    assessment = _assessment_for_report(result)
    if assessment.state is ReliabilityState.INDETERMINATE:
        return "无法判断是否存在差异。"
    if assessment.state is ReliabilityState.DEGRADED:
        return "未检出差异，但不能据此确认一致。"
    if _page_fallback_section_count(result):
        return "未检出受支持的可抽取文字或结构化表格差异。"
    return "未检出受支持的可抽取文字或结构化表格差异。"


def _assessment_for_report(result: DiffResult) -> PairAssessment:
    """Return the attached assessment, conservatively handling legacy results."""

    if result.assessment is not None:
        return result.assessment
    empty_metrics = DocumentQualityMetrics(0, 0, 0, 0, 0, (), (), 1.0)
    return PairAssessment(
        state=ReliabilityState.INDETERMINATE,
        headline="无法判断：比较结果缺少可靠性评估数据",
        reasons=("该比较结果由旧版调用方构造，未包含抽取质量指标。",),
        supported_profile=SUPPORTED_PROFILE,
        allows_no_difference_conclusion=False,
        old_document=empty_metrics,
        new_document=empty_metrics,
    )


def _render_assessment_html(assessment: PairAssessment) -> str:
    """Render the prominent reliability state shown above the report body."""

    reasons = "".join(
        f"<li>{_escape(reason)}</li>"
        for reason in _reader_assessment_reasons(assessment)
    )
    reasons_html = f"<ul>{reasons}</ul>" if reasons else ""
    label = _RELIABILITY_LABELS[assessment.state]
    return (
        f'<section class="assessment-banner assessment-{assessment.state.value}">'
        f"<h2>识别可信度：{_escape(label)}</h2>"
        f"<p>{_escape(assessment.headline)}</p>"
        f"<p>当前支持范围：{_escape(assessment.supported_profile)}</p>"
        f"{reasons_html}"
        "</section>"
    )


def _reader_assessment_reasons(assessment: PairAssessment) -> tuple[str, ...]:
    """Hide parser diagnostics from reader reports while JSON keeps the audit."""

    return tuple(
        reason
        for reason in assessment.reasons
        if _READER_EXTRACTION_WARNING_REASON_RE.fullmatch(reason) is None
    )


def _assessment_to_dict(assessment: PairAssessment) -> dict[str, object]:
    """Serialize reliability facts for downstream audit tooling."""

    return {
        "state": assessment.state.value,
        "headline": assessment.headline,
        "reasons": list(assessment.reasons),
        "supported_profile": assessment.supported_profile,
        "allows_no_difference_conclusion": assessment.allows_no_difference_conclusion,
        "old_document": _document_metrics_to_dict(assessment.old_document),
        "new_document": _document_metrics_to_dict(assessment.new_document),
    }


def _document_metrics_to_dict(metrics: DocumentQualityMetrics) -> dict[str, object]:
    """Serialize one side's measured quality facts without interpretation loss."""

    return {
        "selected_page_count": metrics.selected_page_count,
        "non_empty_page_count": metrics.non_empty_page_count,
        "character_count": metrics.character_count,
        "stable_section_count": metrics.stable_section_count,
        "fallback_section_count": metrics.fallback_section_count,
        "extraction_warnings": list(metrics.extraction_warnings),
        "layout_risk_pages": list(metrics.layout_risk_pages),
        "empty_page_ratio": metrics.empty_page_ratio,
        "average_characters_per_non_empty_page": (
            metrics.average_characters_per_non_empty_page
        ),
        "technical_layout_risk_pages": list(metrics.technical_layout_risk_pages),
        "fragmented_text_pages": list(metrics.fragmented_text_pages),
        "ocr_pages": list(metrics.ocr_pages),
        "image_dominant_pages": list(metrics.image_dominant_pages),  # 独立输出图像页，避免消费者误把 OCR/版面风险当作完整来源信息。
        "parser_route_pages": [
            [route, list(page_numbers)]
            for route, page_numbers in metrics.parser_route_pages
        ],  # JSON 没有元组类型；保留路由名到页号列表的稳定、可机读映射。
        "technical_character_count": metrics.technical_character_count,
        "technical_page_count": metrics.technical_page_count,
        "average_technical_characters_per_page": (
            metrics.average_technical_characters_per_page
        ),
        "duplicate_number_path_count": metrics.duplicate_number_path_count,
        "unstructured_technical_section_count": (
            metrics.unstructured_technical_section_count
        ),
        "uncomparable_table_visual_count": metrics.uncomparable_table_visual_count,
        "ambiguous_table_context_page_count": metrics.ambiguous_table_context_page_count,
        "page_window_consistent": metrics.page_window_consistent,
        "explicit_partial_window": metrics.explicit_partial_window,
        "limited_partial_window": metrics.limited_partial_window,
    }


def _extraction_audit_to_dict(
    audit_pages: tuple[PageExtractionAudit, ...],
) -> list[dict[str, object]]:
    """Serialize the compact route snapshot without page or block source text."""

    # 快照只含报告合同字段；DiffResult 不会因写 JSON 而再次接触完整 ExtractionResult。
    return [
        {
            "page_number": page_audit.page_number,
            "parser_route": page_audit.parser_route.value,
            "image_dominant": page_audit.image_dominant,
            "ocr_used": page_audit.ocr_used,
            "layout_risk": page_audit.layout_risk,
            "block_count": page_audit.block_count,
            "comparison_text_source": page_audit.comparison_text_source,
            "layout_backend_version": page_audit.layout_backend_version,
        }
        for page_audit in audit_pages
    ]


def _provenance_to_dict(provenance: DiffProvenance | None) -> dict[str, object] | None:
    """Serialize reproducibility facts; legacy manually-built results remain explicit."""

    if provenance is None:
        return None
    thresholds = provenance.effective_thresholds
    return {
        "package_version": provenance.package_version,
        "build_commit": provenance.build_commit,
        "supported_profile": provenance.supported_profile,
        "inputs": {
            "old": _input_provenance_to_dict(provenance.old_input),
            "new": _input_provenance_to_dict(provenance.new_input),
        },
        "effective_thresholds": {
            "min_section_match_similarity": thresholds.min_section_match_similarity,
            "max_snippets_per_section": thresholds.max_snippets_per_section,
            "ocr_language": thresholds.ocr_language,
            "layout_backend": thresholds.layout_backend,
            "ocr_native_character_limit": thresholds.ocr_native_character_limit,
            "ocr_minimum_image_coverage": thresholds.ocr_minimum_image_coverage,
            "ocr_native_text_vertical_band_count": (
                thresholds.ocr_native_text_vertical_band_count
            ),
            "ocr_minimum_native_text_vertical_band_coverage": (
                thresholds.ocr_minimum_native_text_vertical_band_coverage
            ),
            "ocr_minimum_result_characters": thresholds.ocr_minimum_result_characters,
            "ocr_render_resolution": thresholds.ocr_render_resolution,
            "ocr_page_timeout_seconds": thresholds.ocr_page_timeout_seconds,
            "ocr_maximum_render_pixels": thresholds.ocr_maximum_render_pixels,
            "quality_min_reliable_characters": thresholds.min_reliable_characters,
            "quality_max_reliable_empty_page_ratio": thresholds.max_reliable_empty_page_ratio,
            "quality_min_pages_for_density_check": thresholds.min_pages_for_density_check,
            "quality_min_average_technical_characters_per_page": (
                thresholds.min_average_technical_characters_per_page
            ),
            "quality_min_pages_for_structure_coverage_check": (
                thresholds.min_pages_for_structure_coverage_check
            ),
            "quality_min_stable_sections_for_full_document": (
                thresholds.min_stable_sections_for_full_document
            ),
            "quality_max_structure_exempt_partial_window_ratio": (
                thresholds.max_structure_exempt_partial_window_ratio
            ),
            "quality_min_lines_for_fragmentation_check": (
                thresholds.min_lines_for_fragmentation_check
            ),
            "quality_max_average_characters_per_fragmented_line": (
                thresholds.max_average_characters_per_fragmented_line
            ),
            "quality_min_single_character_line_ratio": (
                thresholds.min_single_character_line_ratio
            ),
        },
    }


def _input_provenance_to_dict(provenance: object) -> dict[str, object]:
    """Serialize one typed input provenance record."""

    return {
        "path": str(provenance.path),
        "sha256": provenance.sha256,
        "selected_page_window": {
            "start_page": provenance.selected_start_page,
            "end_page": provenance.selected_end_page,
        },
    }


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

    audit_added = _audit_added_snippets(change)
    audit_removed = _audit_removed_snippets(change)
    audit_replaced = _audit_replaced_snippets(change)
    return {
        "change_type": change.change_type,
        "change_label": _CHANGE_LABELS.get(change.change_type, change.change_type),
        "role": change.role,
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
        "match_basis": change.match_basis,
        "match_basis_label": _MATCH_BASIS_LABELS.get(
            change.match_basis,
            change.match_basis,
        ),
        "summary": _change_summary(change),
        "change_nature": _change_natures(change),
        "added_snippets": list(audit_added),
        "removed_snippets": list(audit_removed),
        "replaced_snippets": [
            {"old": pair.old, "new": pair.new} for pair in audit_replaced
        ],
        "display_added_snippets": list(change.added_snippets),
        "display_removed_snippets": list(change.removed_snippets),
        "display_replaced_snippets": [
            {"old": pair.old, "new": pair.new} for pair in change.replaced_snippets
        ],
        "omitted_snippet_count": change.omitted_snippet_count,
        "snippet_audit_complete": True,
    }


def _audit_added_snippets(change: SectionChange) -> list[str]:
    """Return the complete added-occurrence audit, including reader-omitted items."""

    return (
        change.audit_added_snippets
        if change.audit_added_snippets is not None
        else change.added_snippets
    )


def _audit_removed_snippets(change: SectionChange) -> list[str]:
    """Return the complete removed-occurrence audit, including reader-omitted items."""

    return (
        change.audit_removed_snippets
        if change.audit_removed_snippets is not None
        else change.removed_snippets
    )


def _audit_replaced_snippets(change: SectionChange) -> list[SnippetPair]:
    """Return the complete replacement audit, including reader-omitted items."""

    return (
        change.audit_replaced_snippets
        if change.audit_replaced_snippets is not None
        else change.replaced_snippets
    )


def _table_change_to_dict(change: TableChange) -> dict[str, object]:
    """Serialize a paired table and every row finding for audit/reuse."""

    return {
        "change_type": change.change_type,
        "change_label": _CHANGE_LABELS.get(change.change_type, change.change_type),
        "role": change.role,
        "old_titles": _unique_table_titles(change.old_tables),
        "new_titles": _unique_table_titles(change.new_tables),
        "old_pages": [table.page_number for table in change.old_tables],
        "new_pages": [table.page_number for table in change.new_tables],
        "pair_similarity": (
            round(change.similarity, 6)
            if change.old_tables and change.new_tables
            else None
        ),
        "caption_changed": change.caption_changed,
        "row_change_count": len(_material_table_row_changes(change)),
        "review_count": len(_table_review_rows(change)),
        "row_changes": [
            {
                "item": row.item,
                "old_value": row.old_value,
                "new_value": row.new_value,
                "change_type": row.change_type,
            }
            for row in change.row_changes
        ],
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
        "role": section.role,
    }


def _table_visual_to_dict(table: TableVisual) -> dict[str, object]:
    """Serialize table visual metadata without duplicating huge image payloads."""

    return {
        "page_number": table.page_number,
        "table_number": table.table_number,
        "title": table.title,
        "bbox": list(table.bbox),
        "page_bbox": list(table.page_bbox) if table.page_bbox is not None else None,
        "row_texts": list(table.row_texts),
        "grid_summary": table.grid_summary,
        "ocr_status": table.ocr_status,
        "ocr_text_preview": truncate(compact_inline(table.ocr_text), 500),
        "has_embedded_image": bool(table.image_data_uri),
        "is_continuation": table.is_continuation,
        "content_fully_represented": table.content_fully_represented,
        "row_alignment_reliable": table.row_alignment_reliable,
    }
