"""Convert extracted PDF text into protocol-like sections.

The sectioner is heuristic because PDFs do not carry reliable semantic
"chapter" data. It recognizes common Chinese and numeric protocol headings such
as ``第一章``, ``第2节``, ``1``, ``1.1``, ``一、`` and ``(一)``. When no headings
are found, it falls back to page-based chunks instead of pretending it knows the
document structure.
"""

# Codex说明(自动生成)： 从 __future__ 导入 annotations，启用较新的类型标注行为，减少运行期导入或前向引用问题。
from __future__ import annotations

# Codex说明(自动生成)： 导入 re，执行正则匹配和文本规则识别。
import re
# Codex说明(自动生成)： 从 collections 导入 Counter，提供本文件后续流程需要的库能力。
from collections import Counter
# Codex说明(自动生成)： 从 dataclasses 导入 dataclass，声明轻量数据结构并减少样板初始化代码。
from dataclasses import dataclass

# Codex说明(自动生成)： 从 models 导入 ExtractionResult, HeadingInfo, PageText, Section，提供本文件后续流程需要的库能力。
from .models import ExtractionResult, HeadingInfo, PageText, Section
# Codex说明(自动生成)： 从 text_utils 导入 compact_inline, normalize_for_similarity, normalize_line，提供本文件后续流程需要的库能力。
from .text_utils import compact_inline, normalize_for_similarity, normalize_line

# Codex说明(自动生成)： 计算并保存 _CHINESE_NUM，供后续语句继续读取或更新。
_CHINESE_NUM = r"零〇一二三四五六七八九十百千万两0-9\d"

# Codex说明(自动生成)： 声明并保存 _HEADING_PATTERNS，同时保留类型信息方便维护和静态检查。
_HEADING_PATTERNS: tuple[tuple[re.Pattern[str], int, str], ...] = (
    (
        re.compile(rf"^(第\s*[{_CHINESE_NUM}]+\s*章)\s*(.{{0,100}})$"),
        1,
        "chapter",
    ),
    (
        re.compile(rf"^(第\s*[{_CHINESE_NUM}]+\s*节)\s*(.{{0,100}})$"),
        2,
        "section",
    ),
    (
        re.compile(r"^(\d+(?:\.\d+){0,5})(?:[、.)．\s]+)(.{0,120})$"),
        0,
        "numeric",
    ),
    (
        re.compile(r"^([一二三四五六七八九十百千万]+[、.．])\s*(.{1,100})$"),
        2,
        "cn_list",
    ),
    (
        re.compile(r"^[(（]([一二三四五六七八九十百千万0-9\d]+)[)）]\s*(.{1,100})$"),
        3,
        "paren",
    ),
)


# Codex说明(自动生成)： 定义 _OpenSection 类，把相关数据结构、校验规则或操作方法组织在一起。
@dataclass
class _OpenSection:
    """Mutable buffer while pages are being sectioned."""

    # Codex说明(自动生成)： 声明并保存 heading，同时保留类型信息方便维护和静态检查。
    heading: str
    # Codex说明(自动生成)： 声明并保存 title，同时保留类型信息方便维护和静态检查。
    title: str
    # Codex说明(自动生成)： 声明并保存 level，同时保留类型信息方便维护和静态检查。
    level: int
    # Codex说明(自动生成)： 声明并保存 heading_path，同时保留类型信息方便维护和静态检查。
    heading_path: tuple[str, ...]
    # Codex说明(自动生成)： 声明并保存 number_path，同时保留类型信息方便维护和静态检查。
    number_path: tuple[str, ...]
    # Codex说明(自动生成)： 声明并保存 start_page，同时保留类型信息方便维护和静态检查。
    start_page: int
    # Codex说明(自动生成)： 声明并保存 end_page，同时保留类型信息方便维护和静态检查。
    end_page: int
    # Codex说明(自动生成)： 声明并保存 lines，同时保留类型信息方便维护和静态检查。
    lines: list[str]


# Codex说明(自动生成)： 定义函数 section_document，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def section_document(extraction: ExtractionResult) -> list[Section]:
    """Split extracted pages into logical protocol sections.

    Repeated short lines that appear on many pages are treated as headers or
    footers and removed. This reduces false changes caused by page numbers,
    document titles, confidentiality banners, and similar furniture.
    """

    # Codex说明(自动生成)： 计算并保存 cleaned_pages，供后续语句继续读取或更新。
    cleaned_pages = _merge_standalone_heading_lines(_remove_repeating_page_furniture(extraction.pages))
    # Codex说明(自动生成)： 声明并保存 sections，同时保留类型信息方便维护和静态检查。
    sections: list[Section] = []
    # Codex说明(自动生成)： 声明并保存 current，同时保留类型信息方便维护和静态检查。
    current: _OpenSection | None = None
    # Codex说明(自动生成)： 声明并保存 heading_stack，同时保留类型信息方便维护和静态检查。
    heading_stack: list[HeadingInfo] = []
    # Codex说明(自动生成)： 计算并保存 saw_heading，供后续语句继续读取或更新。
    saw_heading = False

    # Codex说明(自动生成)： 遍历 cleaned_pages 中的 page，逐项执行循环体逻辑。
    for page in cleaned_pages:
        # Codex说明(自动生成)： 遍历 page.text.splitlines() 中的 raw_line，逐项执行循环体逻辑。
        for raw_line in page.text.splitlines():
            # Codex说明(自动生成)： 计算并保存 line，供后续语句继续读取或更新。
            line = normalize_line(raw_line)
            # Codex说明(自动生成)： 检查条件 not line，根据结果选择后续执行路径。
            if not line:
                # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
                continue
            # Codex说明(自动生成)： 计算并保存 heading，供后续语句继续读取或更新。
            heading = detect_heading(line)
            # Codex说明(自动生成)： 检查条件 heading，根据结果选择后续执行路径。
            if heading:
                # Codex说明(自动生成)： 计算并保存 saw_heading，供后续语句继续读取或更新。
                saw_heading = True
                # Codex说明(自动生成)： 检查条件 current，根据结果选择后续执行路径。
                if current:
                    # Codex说明(自动生成)： 调用 sections.append 更新列表或集合，把当前步骤产生的数据加入结果。
                    sections.append(_close_section(current, len(sections) + 1))
                # Codex说明(自动生成)： 计算并保存 heading_stack，供后续语句继续读取或更新。
                heading_stack = _updated_stack(heading_stack, heading)
                # Codex说明(自动生成)： 计算并保存 current，供后续语句继续读取或更新。
                current = _OpenSection(
                    heading=heading.raw,
                    title=heading.title or heading.raw,
                    level=heading.level,
                    heading_path=tuple(item.raw for item in heading_stack),
                    number_path=tuple(item.number for item in heading_stack if item.number),
                    start_page=page.page_number,
                    end_page=page.page_number,
                    lines=[],
                )
                # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
                continue

            # Codex说明(自动生成)： 检查条件 current is None，根据结果选择后续执行路径。
            if current is None:
                # Codex说明(自动生成)： 计算并保存 current，供后续语句继续读取或更新。
                current = _OpenSection(
                    heading="文档开头",
                    title="文档开头",
                    level=0,
                    heading_path=("文档开头",),
                    number_path=(),
                    start_page=page.page_number,
                    end_page=page.page_number,
                    lines=[],
                )
            # Codex说明(自动生成)： 更新 current.end_page，把当前配置或计算结果写入对应对象。
            current.end_page = page.page_number
            # Codex说明(自动生成)： 调用 current.lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
            current.lines.append(line)

    # Codex说明(自动生成)： 检查条件 current，根据结果选择后续执行路径。
    if current:
        # Codex说明(自动生成)： 调用 sections.append 更新列表或集合，把当前步骤产生的数据加入结果。
        sections.append(_close_section(current, len(sections) + 1))

    # Codex说明(自动生成)： 计算并保存 meaningful_sections，供后续语句继续读取或更新。
    meaningful_sections = [section for section in sections if section.body.strip()]
    # Codex说明(自动生成)： 检查条件 not saw_heading，根据结果选择后续执行路径。
    if not saw_heading:
        # Codex说明(自动生成)： 返回 _page_fallback_sections(cleaned_pages)，让调用方取得本函数的处理结果。
        return _page_fallback_sections(cleaned_pages)
    # Codex说明(自动生成)： 返回 meaningful_sections，让调用方取得本函数的处理结果。
    return meaningful_sections


# Codex说明(自动生成)： 定义函数 detect_heading，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def detect_heading(line: str) -> HeadingInfo | None:
    """Return heading metadata if a line looks like a protocol heading."""

    # Codex说明(自动生成)： 计算并保存 candidate，供后续语句继续读取或更新。
    candidate = compact_inline(line)
    # Codex说明(自动生成)： 检查条件 not candidate or len(candidate) > 140，根据结果选择后续执行路径。
    if not candidate or len(candidate) > 140:
        # Codex说明(自动生成)： 返回 None，让调用方取得本函数的处理结果。
        return None
    # Codex说明(自动生成)： 检查条件 _looks_like_table_row(candidate)，根据结果选择后续执行路径。
    if _looks_like_table_row(candidate):
        # Codex说明(自动生成)： 返回 None，让调用方取得本函数的处理结果。
        return None

    # Codex说明(自动生成)： 遍历 _HEADING_PATTERNS 中的 (pattern, configured_level, kind)，逐项执行循环体逻辑。
    for pattern, configured_level, kind in _HEADING_PATTERNS:
        # Codex说明(自动生成)： 计算并保存 match，供后续语句继续读取或更新。
        match = pattern.match(candidate)
        # Codex说明(自动生成)： 检查条件 not match，根据结果选择后续执行路径。
        if not match:
            # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
            continue
        # Codex说明(自动生成)： 计算并保存 number，供后续语句继续读取或更新。
        number = match.group(1).strip()
        # Codex说明(自动生成)： 计算并保存 title，供后续语句继续读取或更新。
        title = match.group(2).strip() if match.lastindex and match.lastindex >= 2 else ""
        # Codex说明(自动生成)： 检查条件 kind == 'paren'，根据结果选择后续执行路径。
        if kind == "paren":
            # Codex说明(自动生成)： 计算并保存 number，供后续语句继续读取或更新。
            number = f"({number})"
        # Codex说明(自动生成)： 检查条件 kind == 'numeric'，根据结果选择后续执行路径。
        if kind == "numeric":
            # Codex说明(自动生成)： 检查条件 _looks_like_year_or_decimal_value(number, title)，根据结果选择后续执行路径。
            if _looks_like_year_or_decimal_value(number, title):
                # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
                continue
            # Codex说明(自动生成)： 计算并保存 level，供后续语句继续读取或更新。
            level = number.count(".") + 1
        # Codex说明(自动生成)： 处理前面条件都未命中时的默认分支。
        else:
            # Codex说明(自动生成)： 计算并保存 level，供后续语句继续读取或更新。
            level = configured_level
        # Codex说明(自动生成)： 返回 HeadingInfo(raw=candidate, number=number, title=title, ...，让调用方取得本函数的处理结果。
        return HeadingInfo(raw=candidate, number=number, title=title, level=level)
    # Codex说明(自动生成)： 返回 None，让调用方取得本函数的处理结果。
    return None


# Codex说明(自动生成)： 定义函数 _remove_repeating_page_furniture，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _remove_repeating_page_furniture(pages: list[PageText]) -> list[PageText]:
    """Remove repeated headers/footers that appear on many pages."""

    # Codex说明(自动生成)： 检查条件 len(pages) < 3，根据结果选择后续执行路径。
    if len(pages) < 3:
        # Codex说明(自动生成)： 返回 pages，让调用方取得本函数的处理结果。
        return pages

    # Codex说明(自动生成)： 声明并保存 line_counts，同时保留类型信息方便维护和静态检查。
    line_counts: Counter[str] = Counter()
    # Codex说明(自动生成)： 遍历 pages 中的 page，逐项执行循环体逻辑。
    for page in pages:
        # Codex说明(自动生成)： 计算并保存 unique_lines，供后续语句继续读取或更新。
        unique_lines = {normalize_line(line) for line in page.text.splitlines() if line.strip()}
        # Codex说明(自动生成)： 遍历 unique_lines 中的 line，逐项执行循环体逻辑。
        for line in unique_lines:
            # Codex说明(自动生成)： 检查条件 2 <= len(line) <= 80，根据结果选择后续执行路径。
            if 2 <= len(line) <= 80:
                # Codex说明(自动生成)： 基于旧值更新 line_counts[line]，累积当前循环或处理步骤的结果。
                line_counts[line] += 1

    # Codex说明(自动生成)： 计算并保存 min_repeats，供后续语句继续读取或更新。
    min_repeats = max(3, int(len(pages) * 0.55))
    # Codex说明(自动生成)： 计算并保存 repeated，供后续语句继续读取或更新。
    repeated = {line for line, count in line_counts.items() if count >= min_repeats}
    # Codex说明(自动生成)： 检查条件 not repeated，根据结果选择后续执行路径。
    if not repeated:
        # Codex说明(自动生成)： 返回 pages，让调用方取得本函数的处理结果。
        return pages

    # Codex说明(自动生成)： 声明并保存 cleaned，同时保留类型信息方便维护和静态检查。
    cleaned: list[PageText] = []
    # Codex说明(自动生成)： 遍历 pages 中的 page，逐项执行循环体逻辑。
    for page in pages:
        # Codex说明(自动生成)： 计算并保存 kept_lines，供后续语句继续读取或更新。
        kept_lines = [
            line
            for line in page.text.splitlines()
            if normalize_line(line) not in repeated
        ]
        # Codex说明(自动生成)： 调用 cleaned.append 更新列表或集合，把当前步骤产生的数据加入结果。
        cleaned.append(PageText(page_number=page.page_number, text="\n".join(kept_lines)))
    # Codex说明(自动生成)： 返回 cleaned，让调用方取得本函数的处理结果。
    return cleaned


# Codex说明(自动生成)： 定义函数 _merge_standalone_heading_lines，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _merge_standalone_heading_lines(pages: list[PageText]) -> list[PageText]:
    """Merge headings that PDF extraction split across two lines.

    Many protocols visually show ``1`` and the title beside or below it, but PDF
    extraction can return them as ``1`` followed by ``适用范围`` on the next line.
    Combining only short standalone heading markers keeps the chapter/section
    hierarchy useful without rewriting ordinary paragraphs.
    """

    # Codex说明(自动生成)： 声明并保存 merged_pages，同时保留类型信息方便维护和静态检查。
    merged_pages: list[PageText] = []
    # Codex说明(自动生成)： 遍历 pages 中的 page，逐项执行循环体逻辑。
    for page in pages:
        # Codex说明(自动生成)： 计算并保存 raw_lines，供后续语句继续读取或更新。
        raw_lines = page.text.splitlines()
        # Codex说明(自动生成)： 声明并保存 merged_lines，同时保留类型信息方便维护和静态检查。
        merged_lines: list[str] = []
        # Codex说明(自动生成)： 计算并保存 index，供后续语句继续读取或更新。
        index = 0
        # Codex说明(自动生成)： 当条件 index < len(raw_lines) 成立时，重复执行循环体逻辑。
        while index < len(raw_lines):
            # Codex说明(自动生成)： 计算并保存 line，供后续语句继续读取或更新。
            line = normalize_line(raw_lines[index])
            # Codex说明(自动生成)： 检查条件 not line，根据结果选择后续执行路径。
            if not line:
                # Codex说明(自动生成)： 基于旧值更新 index，累积当前循环或处理步骤的结果。
                index += 1
                # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
                continue
            # Codex说明(自动生成)： 计算并保存 next_line，供后续语句继续读取或更新。
            next_line = _next_non_empty_line(raw_lines, index + 1)
            # Codex说明(自动生成)： 检查条件 next_line and _is_standalone_heading_marker(line) and _...，根据结果选择后续执行路径。
            if next_line and _is_standalone_heading_marker(line) and _can_be_heading_title(next_line):
                # Codex说明(自动生成)： 调用 merged_lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
                merged_lines.append(f"{line} {next_line}")
                # Codex说明(自动生成)： 计算并保存 index，供后续语句继续读取或更新。
                index = _index_after_next_non_empty(raw_lines, index + 1)
                # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
                continue
            # Codex说明(自动生成)： 调用 merged_lines.append 更新列表或集合，把当前步骤产生的数据加入结果。
            merged_lines.append(line)
            # Codex说明(自动生成)： 基于旧值更新 index，累积当前循环或处理步骤的结果。
            index += 1
        # Codex说明(自动生成)： 调用 merged_pages.append 更新列表或集合，把当前步骤产生的数据加入结果。
        merged_pages.append(PageText(page_number=page.page_number, text="\n".join(merged_lines)))
    # Codex说明(自动生成)： 返回 merged_pages，让调用方取得本函数的处理结果。
    return merged_pages


# Codex说明(自动生成)： 定义函数 _updated_stack，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _updated_stack(stack: list[HeadingInfo], heading: HeadingInfo) -> list[HeadingInfo]:
    """Apply a newly detected heading to the hierarchy stack."""

    # Codex说明(自动生成)： 计算并保存 level，供后续语句继续读取或更新。
    level = max(1, heading.level)
    # Codex说明(自动生成)： 计算并保存 trimmed，供后续语句继续读取或更新。
    trimmed = [item for item in stack if item.level < level]
    # Codex说明(自动生成)： 调用 trimmed.append 更新列表或集合，把当前步骤产生的数据加入结果。
    trimmed.append(heading)
    # Codex说明(自动生成)： 返回 trimmed，让调用方取得本函数的处理结果。
    return trimmed


# Codex说明(自动生成)： 定义函数 _close_section，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _close_section(open_section: _OpenSection, index: int) -> Section:
    """Freeze a mutable section buffer into an immutable Section."""

    # Codex说明(自动生成)： 计算并保存 body，供后续语句继续读取或更新。
    body = "\n".join(open_section.lines).strip()
    # Codex说明(自动生成)： 返回 Section(section_id=f'S{index:04d}', heading=open_sectio...，让调用方取得本函数的处理结果。
    return Section(
        section_id=f"S{index:04d}",
        heading=open_section.heading,
        title=open_section.title,
        level=open_section.level,
        heading_path=open_section.heading_path,
        number_path=open_section.number_path,
        start_page=open_section.start_page,
        end_page=open_section.end_page,
        body=body,
    )


# Codex说明(自动生成)： 定义函数 _page_fallback_sections，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _page_fallback_sections(pages: list[PageText]) -> list[Section]:
    """Build page-level sections when no reliable headings were found."""

    # Codex说明(自动生成)： 声明并保存 sections，同时保留类型信息方便维护和静态检查。
    sections: list[Section] = []
    # Codex说明(自动生成)： 遍历 pages 中的 page，逐项执行循环体逻辑。
    for page in pages:
        # Codex说明(自动生成)： 计算并保存 body，供后续语句继续读取或更新。
        body = normalize_for_similarity(page.text).strip()
        # Codex说明(自动生成)： 检查条件 not body，根据结果选择后续执行路径。
        if not body:
            # Codex说明(自动生成)： 跳过本轮剩余逻辑，直接进入下一轮循环判断。
            continue
        # Codex说明(自动生成)： 计算并保存 heading，供后续语句继续读取或更新。
        heading = f"第 {page.page_number} 页"
        # Codex说明(自动生成)： 调用 sections.append 更新列表或集合，把当前步骤产生的数据加入结果。
        sections.append(
            Section(
                section_id=f"P{page.page_number:04d}",
                heading=heading,
                title=heading,
                level=1,
                heading_path=(heading,),
                number_path=(f"page-{page.page_number}",),
                start_page=page.page_number,
                end_page=page.page_number,
                body=body,
            )
        )
    # Codex说明(自动生成)： 返回 sections，让调用方取得本函数的处理结果。
    return sections


# Codex说明(自动生成)： 定义函数 _looks_like_table_row，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _looks_like_table_row(line: str) -> bool:
    """Reject dense table rows that often begin with numbers."""

    # Codex说明(自动生成)： 计算并保存 separators，供后续语句继续读取或更新。
    separators = line.count("|") + line.count("\t")
    # Codex说明(自动生成)： 计算并保存 many_numbers，供后续语句继续读取或更新。
    many_numbers = len(re.findall(r"\d+(?:\.\d+)?", line)) >= 4
    # Codex说明(自动生成)： 返回 separators >= 2 or (many_numbers and len(line) > 40)，让调用方取得本函数的处理结果。
    return separators >= 2 or (many_numbers and len(line) > 40)


# Codex说明(自动生成)： 定义函数 _looks_like_year_or_decimal_value，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _looks_like_year_or_decimal_value(number: str, title: str) -> bool:
    """Avoid treating dates or plain numeric values as section headings."""

    # Codex说明(自动生成)： 检查条件 '.' not in number and len(number) == 4 and number.start...，根据结果选择后续执行路径。
    if "." not in number and len(number) == 4 and number.startswith(("19", "20")):
        # Codex说明(自动生成)： 返回 True，让调用方取得本函数的处理结果。
        return True
    # Codex说明(自动生成)： 检查条件 not title，根据结果选择后续执行路径。
    if not title:
        # Codex说明(自动生成)： 返回 True，让调用方取得本函数的处理结果。
        return True
    # Codex说明(自动生成)： 检查条件 len(title) <= 2 and re.fullmatch('[\\d.%:/-]+', title)，根据结果选择后续执行路径。
    if len(title) <= 2 and re.fullmatch(r"[\d.%:/-]+", title):
        # Codex说明(自动生成)： 返回 True，让调用方取得本函数的处理结果。
        return True
    # Codex说明(自动生成)： 返回 False，让调用方取得本函数的处理结果。
    return False


# Codex说明(自动生成)： 定义函数 _is_standalone_heading_marker，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _is_standalone_heading_marker(line: str) -> bool:
    """Return True for bare heading markers such as ``1`` or ``第一章``."""

    # Codex说明(自动生成)： 计算并保存 numeric，供后续语句继续读取或更新。
    numeric = re.fullmatch(r"(\d+(?:\.\d+){0,5})[.)．]?", line)
    # Codex说明(自动生成)： 检查条件 numeric，根据结果选择后续执行路径。
    if numeric:
        # Codex说明(自动生成)： 计算并保存 number，供后续语句继续读取或更新。
        number = numeric.group(1)
        # Codex说明(自动生成)： 检查条件 '.' not in number and len(number) == 4 and number.start...，根据结果选择后续执行路径。
        if "." not in number and len(number) == 4 and number.startswith(("19", "20")):
            # Codex说明(自动生成)： 返回 False，让调用方取得本函数的处理结果。
            return False
        # Codex说明(自动生成)： 返回 True，让调用方取得本函数的处理结果。
        return True
    # Codex说明(自动生成)： 返回 bool(re.fullmatch(f'第\\s*[{_CHINESE_NUM}]+\\s*[章节]', li...，让调用方取得本函数的处理结果。
    return bool(re.fullmatch(rf"第\s*[{_CHINESE_NUM}]+\s*[章节]", line))


# Codex说明(自动生成)： 定义函数 _can_be_heading_title，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _can_be_heading_title(line: str) -> bool:
    """Guard against merging table rows or another heading as a title."""

    # Codex说明(自动生成)： 计算并保存 title，供后续语句继续读取或更新。
    title = normalize_line(line)
    # Codex说明(自动生成)： 检查条件 not title or len(title) > 100，根据结果选择后续执行路径。
    if not title or len(title) > 100:
        # Codex说明(自动生成)： 返回 False，让调用方取得本函数的处理结果。
        return False
    # Codex说明(自动生成)： 检查条件 detect_heading(title)，根据结果选择后续执行路径。
    if detect_heading(title):
        # Codex说明(自动生成)： 返回 False，让调用方取得本函数的处理结果。
        return False
    # Codex说明(自动生成)： 返回 not _looks_like_table_row(title)，让调用方取得本函数的处理结果。
    return not _looks_like_table_row(title)


# Codex说明(自动生成)： 定义函数 _next_non_empty_line，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _next_non_empty_line(lines: list[str], start_index: int) -> str | None:
    """Find the next non-empty normalized line on the same page."""

    # Codex说明(自动生成)： 遍历 range(start_index, len(lines)) 中的 index，逐项执行循环体逻辑。
    for index in range(start_index, len(lines)):
        # Codex说明(自动生成)： 计算并保存 normalized，供后续语句继续读取或更新。
        normalized = normalize_line(lines[index])
        # Codex说明(自动生成)： 检查条件 normalized，根据结果选择后续执行路径。
        if normalized:
            # Codex说明(自动生成)： 返回 normalized，让调用方取得本函数的处理结果。
            return normalized
    # Codex说明(自动生成)： 返回 None，让调用方取得本函数的处理结果。
    return None


# Codex说明(自动生成)： 定义函数 _index_after_next_non_empty，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _index_after_next_non_empty(lines: list[str], start_index: int) -> int:
    """Return the index immediately after the next non-empty line."""

    # Codex说明(自动生成)： 遍历 range(start_index, len(lines)) 中的 index，逐项执行循环体逻辑。
    for index in range(start_index, len(lines)):
        # Codex说明(自动生成)： 检查条件 normalize_line(lines[index])，根据结果选择后续执行路径。
        if normalize_line(lines[index]):
            # Codex说明(自动生成)： 返回 index + 1，让调用方取得本函数的处理结果。
            return index + 1
    # Codex说明(自动生成)： 返回 len(lines)，让调用方取得本函数的处理结果。
    return len(lines)
