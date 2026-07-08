"""Build paragraph layout regions from visual PDF text lines."""

from __future__ import annotations  # 允许在类型注解里直接引用未来才定义的类型，保持运行时更轻量。

import re  # 用正则识别标题、断词换行和正文开头形态。

from .models import LayoutLine, LayoutRegion  # 复用统一的数据模型，避免报告层需要适配多套结构。
from .sectioning import detect_heading  # 复用现有章节标题启发式，保证 layout 和正文分节口径接近。
from .text_utils import compact_inline  # 复用项目的空白压缩逻辑，减少 PDF 抽取换行差异。


def build_paragraph_regions(
    lines: list[LayoutLine],
    occupied_bboxes: list[tuple[float, float, float, float] | tuple[int, float, float, float, float]],
) -> list[LayoutRegion]:
    """Merge nearby visual text lines into readable paragraph regions."""

    sorted_lines = sorted(lines, key=lambda line: (line.page_number, line.bbox[1], line.bbox[0]))  # 按阅读顺序处理行。
    regions: list[LayoutRegion] = []  # 保存最终段落区域。
    current_lines: list[LayoutLine] = []  # 保存当前段落包含的原始行。
    current_text = ""  # 保存当前段落已经合并出的可读文本。
    previous_line: LayoutLine | None = None  # 记录上一条正文行，用于判断是否另起段落。

    for line in sorted_lines:  # 逐行扫描视觉文本。
        text = compact_inline(line.text)  # 清理多余空白，避免无意义换行进入段落。
        if not text:  # 空行没有可比较内容。
            continue  # 跳过空行。
        if _line_overlaps_occupied_region(line, occupied_bboxes):  # 表格、公式等区域已经单独建模。
            continue  # 不把视觉对象里的文字重复并入正文段落。
        if _looks_like_heading_line(line):  # 标题由 section/layout 层单独处理。
            continue  # 标题不应变成正文段落。
        if previous_line is None or _starts_new_paragraph(previous_line, line):  # 新页或大行距表示段落边界。
            if current_lines:  # 已经累积了上一段。
                regions.append(_paragraph_region(current_lines, current_text))  # 关闭上一段并保存。
            current_lines = [line]  # 以当前行开始新段落。
            current_text = text  # 新段落文本从当前行开始。
        else:  # 当前行属于上一段。
            current_lines.append(line)  # 记录当前行，后面用于计算合并 bbox。
            current_text = _append_wrapped_line(current_text, text)  # 合并普通换行或英文断词换行。
        previous_line = line  # 更新上一行指针。

    if current_lines:  # 循环结束后还有最后一段未保存。
        regions.append(_paragraph_region(current_lines, current_text))  # 保存最后一个段落。
    return regions  # 返回段落区域列表。


def _looks_like_heading_line(line: LayoutLine) -> bool:
    """Return True when a visual line is likely a heading instead of prose."""

    text = compact_inline(line.text)  # 标题判断使用压缩后的文本。
    if not text:  # 空文本不是标题。
        return False  # 返回非标题。
    if line.size >= 13.5 and len(text) <= 160:  # 大字号短行通常是章节标题。
        return True  # 标记为标题。
    return detect_heading(text) is not None  # 复用正文分节器识别普通字号章节标题。


def _starts_new_paragraph(previous_line: LayoutLine, line: LayoutLine) -> bool:
    """Return True when two visual lines should not be merged."""

    if previous_line.page_number != line.page_number:  # 跨页时保守地另起段落。
        return True  # 新页另起段落。
    previous_bottom = previous_line.bbox[3]  # 取上一行底部坐标。
    current_top = line.bbox[1]  # 取当前行顶部坐标。
    vertical_gap = current_top - previous_bottom  # 计算两行之间的垂直间隔。
    normal_gap = max(previous_line.size, line.size) * 1.35  # 正常换行间距跟字号相关。
    if vertical_gap > max(16.0, normal_gap):  # 大于阈值说明中间有段落空白。
        return True  # 另起段落。
    previous_left = previous_line.bbox[0]  # 上一行左边界。
    current_left = line.bbox[0]  # 当前行左边界。
    if abs(current_left - previous_left) > 38.0 and not previous_line.text.rstrip().endswith("-"):  # 缩进突变通常是新段。
        return True  # 另起段落。
    return False  # 默认认为是同一段的自然换行。


def _append_wrapped_line(current_text: str, next_text: str) -> str:
    """Append one wrapped PDF line to an existing paragraph."""

    if current_text.endswith("-") and re.search(r"[A-Za-z]-$", current_text):  # 英文单词断词换行以连字符结尾。
        return current_text[:-1] + next_text  # 删除断词连字符后无空格拼接。
    return f"{current_text} {next_text}"  # 普通换行用空格拼接。


def _paragraph_region(lines: list[LayoutLine], text: str) -> LayoutRegion:
    """Create one paragraph region from merged lines."""

    bbox = _union_bbox([line.bbox for line in lines])  # 段落 bbox 覆盖所有组成行。
    first_line = lines[0]  # 第一行提供页码和页面尺寸。
    return LayoutRegion(  # 构造报告层可消费的段落区域。
        region_id=f"p{first_line.page_number}-{round(bbox[1], 1)}-{round(bbox[0], 1)}",  # ID 用页码和坐标保持稳定。
        region_type="paragraph",  # 标记为正文段落。
        page_number=first_line.page_number,  # 保留源 PDF 页码。
        bbox=bbox,  # 保存段落边界。
        text=compact_inline(text),  # 保存可读段落文本。
        section_context="",  # 由上层 layout_extractor 根据最近标题补上下文。
        page_width=first_line.page_width,  # 保存页面宽度供报告审计。
        page_height=first_line.page_height,  # 保存页面高度供报告审计。
    )


def _line_overlaps_occupied_region(
    line: LayoutLine,
    occupied_bboxes: list[tuple[float, float, float, float] | tuple[int, float, float, float, float]],
) -> bool:
    """Return True when a line is inside a table/formula/figure region."""

    for occupied in occupied_bboxes:  # 检查每个已占用视觉区域。
        page_number, bbox = _normalize_occupied_bbox(occupied)  # 兼容带页码和不带页码两种输入。
        if page_number is not None and page_number != line.page_number:  # 带页码的区域只作用于同页。
            continue  # 跨页不比较重叠。
        if _bbox_overlap_ratio(line.bbox, bbox) >= 0.45:  # 行的大部分面积落入视觉区域时视为已占用。
            return True  # 告诉调用方跳过该行。
    return False  # 没有明显重叠。


def _normalize_occupied_bbox(
    occupied: tuple[float, float, float, float] | tuple[int, float, float, float, float],
) -> tuple[int | None, tuple[float, float, float, float]]:
    """Return optional page number plus bbox from an occupied-region tuple."""

    if len(occupied) == 5:  # 五元组约定为 page_number + bbox。
        return int(occupied[0]), (float(occupied[1]), float(occupied[2]), float(occupied[3]), float(occupied[4]))  # 拆出页码和坐标。
    return None, (float(occupied[0]), float(occupied[1]), float(occupied[2]), float(occupied[3]))  # 四元组只包含 bbox。


def _bbox_overlap_ratio(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    """Return overlap area divided by the first bbox area."""

    left = max(first[0], second[0])  # 交集左边界。
    top = max(first[1], second[1])  # 交集上边界。
    right = min(first[2], second[2])  # 交集右边界。
    bottom = min(first[3], second[3])  # 交集下边界。
    if right <= left or bottom <= top:  # 没有实际交集。
        return 0.0  # 返回零重叠。
    intersection = (right - left) * (bottom - top)  # 计算交集面积。
    first_area = max(1.0, (first[2] - first[0]) * (first[3] - first[1]))  # 避免零面积除法。
    return intersection / first_area  # 返回相对第一块区域的覆盖比例。


def _union_bbox(bboxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    """Return a bbox covering all input boxes."""

    return (  # 组合所有边界坐标。
        min(bbox[0] for bbox in bboxes),  # 最左边界。
        min(bbox[1] for bbox in bboxes),  # 最上边界。
        max(bbox[2] for bbox in bboxes),  # 最右边界。
        max(bbox[3] for bbox in bboxes),  # 最下边界。
    )
