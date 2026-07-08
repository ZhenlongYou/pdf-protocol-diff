"""Extract layout-aware regions from protocol PDFs."""

from __future__ import annotations  # 延迟解析类型注解，降低导入时的依赖压力。

import hashlib  # 用于给区域文本生成稳定哈希，方便 JSON 审计。
import re  # 用于识别公式、表格表头和标题形态。
from dataclasses import dataclass, replace  # dataclass 保存抽取结果，replace 用于补顺序号和上下文。
from pathlib import Path  # 统一处理 PDF 路径。

from .models import LayoutLine, LayoutRegion  # 使用项目统一 layout 数据模型。
from .paragraph_builder import build_paragraph_regions  # 复用段落合并逻辑。
from .sectioning import detect_heading  # 复用章节标题识别规则。
from .text_utils import compact_inline  # 复用空白规整逻辑。


@dataclass(frozen=True)
class LayoutExtraction:
    """Layout-aware extraction result for one PDF."""

    pdf_path: Path  # 源 PDF 绝对路径。
    regions: list[LayoutRegion]  # 抽取出的标题、正文、表格和公式区域。
    warnings: list[str]  # 非致命抽取警告。


def extract_layout_regions(
    pdf_path: str | Path,
    start_page: int | None = None,
    end_page: int | None = None,
) -> LayoutExtraction:
    """Extract typed layout regions from a one-based inclusive page range."""

    path = Path(pdf_path).expanduser().resolve()  # 统一为绝对路径，报告定位更清楚。
    if not path.exists():  # 入口层直接检查文件存在性。
        raise FileNotFoundError(f"PDF 文件不存在: {path}")  # 给用户明确的路径错误。
    try:  # PyMuPDF 提供文字 span、矢量线条和区域截图能力。
        import fitz  # type: ignore[import-not-found]  # 延迟导入，缺依赖时由上层生成清晰警告。
    except ModuleNotFoundError as exc:  # 缺少 PyMuPDF 时无法做 layout-aware 区域比较。
        raise RuntimeError("缺少依赖 PyMuPDF，请运行 .venv/bin/python -m pip install -r requirements.txt") from exc  # 抛出可操作错误。

    warnings: list[str] = []  # 收集单页抽取警告。
    document = fitz.open(str(path))  # 打开 PDF 文档。
    try:  # 确保异常时也关闭文件句柄。
        total_pages = document.page_count  # PyMuPDF 页数。
        selected_start, selected_end = _resolve_page_range(total_pages, start_page, end_page, path.name)  # 校验用户页码。
        regions: list[LayoutRegion] = []  # 汇总所有页面区域。
        for page_number in range(selected_start, selected_end + 1):  # 按 1-based 页码遍历用户范围。
            page = document.load_page(page_number - 1)  # PyMuPDF 使用 0-based 页索引。
            try:  # 单页 layout 失败不应吞掉整份报告。
                regions.extend(_extract_page_regions(page, page_number))  # 抽取该页的 typed regions。
            except Exception as exc:  # 页面结构异常时保留警告。
                warnings.append(f"{path.name}: 第 {page_number} 页 layout 区域抽取失败: {exc}")  # 记录页码和原因。
    finally:
        document.close()  # 释放 PDF 文件句柄。
    ordered_regions = _assign_order_and_context(regions)  # 排序并补充最近章节上下文。
    return LayoutExtraction(pdf_path=path, regions=ordered_regions, warnings=warnings)  # 返回可比较的 layout 结果。


def _extract_page_regions(page: object, page_number: int) -> list[LayoutRegion]:
    """Extract typed regions from one PyMuPDF page."""

    lines = _extract_layout_lines(page, page_number)  # 先把 span 重建成视觉行。
    table_regions = _extract_table_regions(page, lines, page_number)  # 表格优先识别，避免其文字污染正文段落。
    occupied = [_occupied_tuple(region) for region in table_regions]  # 表格 bbox 作为已占用区域。
    formula_regions = _extract_formula_regions(lines, occupied)  # 在非表格区域中识别公式。
    occupied.extend(_occupied_tuple(region) for region in formula_regions)  # 公式 bbox 也从段落中排除。
    heading_regions = _extract_heading_regions(lines, occupied)  # 标题作为独立 region 参与匹配。
    paragraph_regions = build_paragraph_regions(lines, occupied)  # 剩余正文行合并为段落。
    return [*table_regions, *formula_regions, *heading_regions, *paragraph_regions]  # 返回该页所有区域。


def _extract_layout_lines(page: object, page_number: int) -> list[LayoutLine]:
    """Reconstruct visual text lines from PyMuPDF spans."""

    page_width = float(getattr(page, "rect").width)  # 页面宽度用于页边和报告审计。
    page_height = float(getattr(page, "rect").height)  # 页面高度用于页边和报告审计。
    text_dict = page.get_text("dict")  # PyMuPDF dict 输出包含 block/line/span 层级。
    layout_lines: list[LayoutLine] = []  # 保存重建后的文本行。
    for block in text_dict.get("blocks", []):  # 遍历页面块。
        if block.get("type") != 0:  # type 0 是文本块。
            continue  # 图片块或其它对象不在这里处理。
        for raw_line in block.get("lines", []):  # 遍历文本块里的视觉行。
            spans = [span for span in raw_line.get("spans", []) if compact_inline(span.get("text", ""))]  # 丢掉空 span。
            if not spans:  # 没有可见文字时跳过。
                continue  # 跳过空行。
            spans.sort(key=lambda span: float(span.get("bbox", (0, 0, 0, 0))[0]))  # 按 x 坐标恢复阅读顺序。
            text = compact_inline(" ".join(str(span.get("text", "")) for span in spans))  # 用空格连接同一行 span。
            if not text:  # 连接后仍为空则跳过。
                continue  # 跳过空行。
            bboxes = [tuple(float(value) for value in span.get("bbox", (0, 0, 0, 0))) for span in spans]  # 收集 span bbox。
            sizes = [float(span.get("size", 0.0) or 0.0) for span in spans]  # 收集 span 字号。
            fonts = [str(span.get("font", "")) for span in spans]  # 收集字体名。
            baselines = [float(span.get("origin", (0.0, span.get("bbox", (0, 0, 0, 0))[1]))[1]) for span in spans]  # 收集基线。
            layout_lines.append(  # 添加一条统一视觉行。
                LayoutLine(
                    page_number=page_number,  # 源 PDF 页码。
                    text=text,  # 合并后的行文本。
                    bbox=_union_bbox(bboxes),  # 行级 bbox 覆盖所有 span。
                    font=_dominant_font(fonts),  # 主要字体用于标题判断。
                    size=sum(sizes) / max(1, len(sizes)),  # 平均字号足够支持启发式分类。
                    span_count=len(spans),  # span 数量用于识别多列/表格行。
                    baseline_spread=(max(baselines) - min(baselines)) if baselines else 0.0,  # 公式上下标会提高基线离散度。
                    page_width=page_width,  # 页面宽度。
                    page_height=page_height,  # 页面高度。
                )
            )
    return sorted(layout_lines, key=lambda line: (line.page_number, line.bbox[1], line.bbox[0]))  # 返回阅读顺序行。


def _extract_table_regions(page: object, lines: list[LayoutLine], page_number: int) -> list[LayoutRegion]:
    """Detect table regions from vector grids and table-like text."""

    regions: list[LayoutRegion] = []  # 保存识别出的表格区域。
    for bbox in _table_bboxes_from_drawings(page):  # 优先使用真实线框表格。
        text = _text_inside_bbox(lines, bbox)  # 收集 bbox 内文本作为匹配身份。
        if not text:  # 空线框没有可比内容。
            continue  # 跳过空表格候选。
        regions.append(_layout_region("table", page_number, bbox, text, lines[0] if lines else None))  # 添加表格区域。
    for bbox in _table_bboxes_from_text(lines):  # 没有线框或线框漏掉时使用文本表头兜底。
        if any(_bbox_overlap_ratio(bbox, region.bbox) > 0.35 for region in regions):  # 避免重复同一张表。
            continue  # 已有相近表格。
        text = _text_inside_bbox(lines, bbox)  # 提取文本表格内容。
        regions.append(_layout_region("table", page_number, bbox, text, lines[0] if lines else None))  # 添加文本表格区域。
    return regions  # 返回表格区域。


def _table_bboxes_from_drawings(page: object) -> list[tuple[float, float, float, float]]:
    """Return likely table bboxes from horizontal and vertical PDF lines."""

    horizontal: list[tuple[float, float, float, float]] = []  # 保存横线段。
    vertical: list[tuple[float, float, float, float]] = []  # 保存竖线段。
    try:  # 部分 PDF 或 PyMuPDF 版本可能不支持 get_drawings。
        drawings = page.get_drawings()  # 获取矢量绘图对象。
    except Exception:  # 绘图读取失败时返回空候选。
        return []  # 没有线框候选。
    for drawing in drawings:  # 遍历绘图对象。
        for item in drawing.get("items", []):  # 遍历绘图命令。
            segment = _line_segment_from_drawing_item(item)  # 只关心直线段。
            if segment is None:  # 非直线对象不是表格线。
                continue  # 跳过。
            x0, y0, x1, y1 = segment  # 拆出线段坐标。
            if abs(y1 - y0) <= 1.5 and abs(x1 - x0) >= 40.0:  # 足够长的水平线。
                horizontal.append(segment)  # 记录横线。
            if abs(x1 - x0) <= 1.5 and abs(y1 - y0) >= 24.0:  # 足够长的垂直线。
                vertical.append(segment)  # 记录竖线。
    if len(horizontal) < 2 or len(vertical) < 2:  # 网格表至少需要两条横线和两条竖线。
        return []  # 不构造表格 bbox。
    return _cluster_table_line_bboxes(horizontal, vertical)  # 按连通网格聚类，避免一页多表被合成一个大区域。


def _cluster_table_line_bboxes(
    horizontal: list[tuple[float, float, float, float]],
    vertical: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    """Group connected table ruling lines into separate table bboxes."""

    components: list[list[tuple[str, tuple[float, float, float, float]]]] = []  # 每个 component 保存线段方向和坐标。
    for segment in [("h", item) for item in horizontal] + [("v", item) for item in vertical]:  # 合并横线和竖线候选。
        touching_indexes = [
            index
            for index, component in enumerate(components)
            if _segment_touches_component(segment[1], component)
        ]  # 找到与当前线段相连或足够接近的已有 component。
        if not touching_indexes:  # 当前线段不属于已有网格。
            components.append([segment])  # 新建一个 component。
            continue  # 处理下一条线。
        first_index = touching_indexes[0]  # 选第一个相连 component 作为合并目标。
        components[first_index].append(segment)  # 把当前线段加入目标 component。
        for merge_index in reversed(touching_indexes[1:]):  # 如果同时连接多个 component，需要合并它们。
            components[first_index].extend(components.pop(merge_index))  # 后面的 component 合并进第一个。

    bboxes: list[tuple[float, float, float, float]] = []  # 保存最终表格 bbox。
    for component in components:  # 检查每个连通线框。
        horizontal_count = sum(1 for direction, _segment in component if direction == "h")  # 统计横线数量。
        vertical_count = sum(1 for direction, _segment in component if direction == "v")  # 统计竖线数量。
        if horizontal_count < 2 or vertical_count < 2:  # 表格网格至少需要两个方向都有多条线。
            continue  # 过滤装饰线或单轴图形线。
        bbox = _expanded_bbox(_union_bbox([segment for _direction, segment in component]), padding=3.0)  # 生成当前 component 的 bbox。
        if (bbox[2] - bbox[0]) < 80.0 or (bbox[3] - bbox[1]) < 35.0:  # 过小线框通常不是表格。
            continue  # 过滤小候选。
        bboxes.append(bbox)  # 保留有效表格候选。
    return sorted(bboxes, key=lambda bbox: (bbox[1], bbox[0]))  # 按页面阅读顺序返回。


def _segment_touches_component(
    segment: tuple[float, float, float, float],
    component: list[tuple[str, tuple[float, float, float, float]]],
) -> bool:
    """Return True when a line segment belongs to an existing grid component."""

    segment_bbox = _expanded_bbox(_normalized_segment_bbox(segment), padding=6.0)  # 线段本身面积很薄，需要扩展后判断相交。
    component_bbox = _expanded_bbox(_union_bbox([item for _direction, item in component]), padding=6.0)  # component bbox 同样外扩以容忍断线。
    return _bbox_overlap_ratio(segment_bbox, component_bbox) > 0.0  # 外扩 bbox 相交即视为同一网格候选。


def _normalized_segment_bbox(
    segment: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Return a bbox with sorted coordinates for one line segment."""

    x0, y0, x1, y1 = segment  # 拆出线段两端坐标。
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))  # 统一成左上右下格式。


def _line_segment_from_drawing_item(item: object) -> tuple[float, float, float, float] | None:
    """Extract a straight line segment from one PyMuPDF drawing item."""

    if not isinstance(item, tuple) or not item:  # 绘图 item 必须是元组。
        return None  # 非预期结构跳过。
    if item[0] != "l" or len(item) < 3:  # PyMuPDF 用 "l" 表示 line-to。
        return None  # 只处理直线。
    start = item[1]  # 起点对象。
    end = item[2]  # 终点对象。
    return (float(start.x), float(start.y), float(end.x), float(end.y))  # 转成普通坐标元组。


def _table_bboxes_from_text(lines: list[LayoutLine]) -> list[tuple[float, float, float, float]]:
    """Return table-like bboxes from repeated column header text."""

    bboxes: list[tuple[float, float, float, float]] = []  # 保存文本启发式表格区域。
    for index, line in enumerate(lines):  # 扫描每一行找表头。
        if not _looks_like_table_header_line(line.text):  # 不是表头就继续。
            continue  # 跳过普通行。
        table_lines = [line]  # 表头行一定属于表格。
        for following in lines[index + 1 : index + 8]:  # 最多向后吸收七行，避免吞掉整页正文。
            if following.page_number != line.page_number:  # 跨页停止。
                break  # 停止吸收。
            if following.bbox[1] - table_lines[-1].bbox[3] > 28.0:  # 大间距表示表格结束。
                break  # 停止吸收。
            table_lines.append(following)  # 吸收同一区域后续行。
        if len(table_lines) >= 2:  # 至少两行才像表格。
            bboxes.append(_expanded_bbox(_union_bbox([item.bbox for item in table_lines]), padding=4.0))  # 生成表格 bbox。
    return bboxes  # 返回文本表格候选。


def _extract_formula_regions(
    lines: list[LayoutLine],
    occupied: list[tuple[int, float, float, float, float]],
) -> list[LayoutRegion]:
    """Detect formula regions outside table bboxes."""

    regions: list[LayoutRegion] = []  # 保存公式区域。
    for line in lines:  # 公式通常对应单条视觉行。
        if _line_overlaps_occupied(line, occupied):  # 表格内的数学符号属于表格，不单独建公式。
            continue  # 跳过已占用行。
        if not _looks_like_formula_line(line):  # 不是公式行。
            continue  # 跳过普通行。
        regions.append(_layout_region("formula", line.page_number, _expanded_bbox(line.bbox, 4.0), line.text, line))  # 添加公式区域。
    return regions  # 返回公式区域。


def _extract_heading_regions(
    lines: list[LayoutLine],
    occupied: list[tuple[int, float, float, float, float]],
) -> list[LayoutRegion]:
    """Detect heading regions outside table/formula bboxes."""

    regions: list[LayoutRegion] = []  # 保存标题区域。
    for line in lines:  # 逐行判断标题。
        if _line_overlaps_occupied(line, occupied):  # 表格/公式内文字不作为标题。
            continue  # 跳过。
        if not _looks_like_heading_line(line):  # 不是章节标题。
            continue  # 跳过。
        regions.append(_layout_region("heading", line.page_number, line.bbox, line.text, line))  # 添加标题区域。
    return regions  # 返回标题区域。


def _looks_like_heading_line(line: LayoutLine) -> bool:
    """Return True when a line is likely a structural heading."""

    text = compact_inline(line.text)  # 标题判断使用压缩文本。
    if not text or len(text) > 180:  # 空行或过长句子不像标题。
        return False  # 返回非标题。
    if detect_heading(text) is not None:  # 现有章节规则命中。
        return True  # 标记为标题。
    return line.size >= 14.0 and not text.endswith(".")  # 大字号无句号短行作为标题兜底。


def _looks_like_table_header_line(text: str) -> bool:
    """Return True when a line looks like a protocol table header."""

    words = set(re.findall(r"[A-Za-z]+", compact_inline(text).casefold()))  # 提取英文词集合。
    header_words = {"parameter", "symbol", "value", "values", "unit", "units", "min", "max", "condition"}  # 常见表头词。
    return len(words & header_words) >= 3  # 至少三个表头词才视为表格。


def _looks_like_formula_line(line: LayoutLine) -> bool:
    """Return True when a line is likely a displayed formula."""

    text = compact_inline(line.text)  # 公式判断使用压缩文本。
    if len(text) < 12:  # 太短的符号片段不单独成公式。
        return False  # 返回非公式。
    operator_count = len(re.findall(r"<=|>=|≤|≥|=|[×*/]", text))  # 统计公式运算符。
    number_count = len(re.findall(r"\d+(?:\.\d+)?", text))  # 统计数字数量。
    has_formula_name = bool(re.search(r"[A-Za-z]+\(.*?\)", text))  # RL(f) 这类函数形态是强信号。
    return operator_count >= 2 and number_count >= 2 and (has_formula_name or line.baseline_spread > 1.5)  # 综合判断公式。


def _layout_region(
    region_type: str,
    page_number: int,
    bbox: tuple[float, float, float, float],
    text: str,
    line: LayoutLine | None,
) -> LayoutRegion:
    """Create a LayoutRegion with stable metadata."""

    cleaned_text = compact_inline(text)  # 区域文本统一空白。
    page_width = line.page_width if line else 0.0  # 从示例行继承页面宽度。
    page_height = line.page_height if line else 0.0  # 从示例行继承页面高度。
    region_hash = hashlib.sha1(f"{region_type}:{cleaned_text}".encode("utf-8")).hexdigest()[:16]  # 文本哈希用于审计。
    return LayoutRegion(  # 构造统一 layout region。
        region_id=f"{region_type}-{page_number}-{round(bbox[1], 1)}-{region_hash}",  # ID 组合类型、页码、坐标和内容。
        region_type=region_type,  # 区域类型。
        page_number=page_number,  # 源页码。
        bbox=bbox,  # 区域 bbox。
        text=cleaned_text,  # 区域文本。
        section_context="",  # 后续统一补最近标题。
        image_hash=region_hash if region_type in {"table", "formula", "figure"} else "",  # 视觉区域使用同一哈希做身份线索。
        page_width=page_width,  # 页面宽度。
        page_height=page_height,  # 页面高度。
    )


def _assign_order_and_context(regions: list[LayoutRegion]) -> list[LayoutRegion]:
    """Sort regions and attach nearest heading context."""

    priority = {"heading": 0, "paragraph": 1, "table": 2, "formula": 3, "figure": 4}  # 同坐标附近标题优先。
    ordered = sorted(regions, key=lambda region: (region.page_number, region.bbox[1], region.bbox[0], priority.get(region.region_type, 9)))  # 按阅读顺序排序。
    contextualized: list[LayoutRegion] = []  # 保存补充上下文后的区域。
    current_heading = ""  # 最近标题文本。
    for order_index, region in enumerate(ordered, start=1):  # 重新赋稳定顺序号。
        if region.region_type == "heading" and region.text:  # 标题区域更新上下文。
            current_heading = region.text  # 保存最近标题。
        context = region.text if region.region_type == "heading" else current_heading  # 非标题区域继承最近标题。
        contextualized.append(replace(region, order_index=order_index, section_context=context))  # 写入顺序和上下文。
    return contextualized  # 返回最终区域。


def _resolve_page_range(
    total_pages: int,
    start_page: int | None,
    end_page: int | None,
    pdf_name: str,
) -> tuple[int, int]:
    """Validate and normalize one-based page range values."""

    start = 1 if start_page is None else start_page  # 未指定起始页时从第一页开始。
    end = total_pages if end_page is None else end_page  # 未指定结束页时到最后一页。
    if start < 1 or end < 1 or start > end or end > total_pages:  # 页码必须在 PDF 范围内且起止有序。
        raise ValueError(f"{pdf_name}: 页码范围无效 {start}-{end}，PDF 共 {total_pages} 页")  # 抛出明确错误。
    return start, end  # 返回规范化范围。


def _occupied_tuple(region: LayoutRegion) -> tuple[int, float, float, float, float]:
    """Return a page-qualified bbox tuple for overlap checks."""

    return (region.page_number, region.bbox[0], region.bbox[1], region.bbox[2], region.bbox[3])  # 加页码避免跨页误判。


def _line_overlaps_occupied(
    line: LayoutLine,
    occupied: list[tuple[int, float, float, float, float]],
) -> bool:
    """Return True when a line overlaps any occupied region on the same page."""

    for page_number, x0, y0, x1, y1 in occupied:  # 遍历带页码的占用区域。
        if page_number != line.page_number:  # 只比较同页区域。
            continue  # 跨页跳过。
        if _bbox_overlap_ratio(line.bbox, (x0, y0, x1, y1)) >= 0.35:  # 重叠足够大说明行属于该视觉区域。
            return True  # 返回已占用。
    return False  # 没有占用重叠。


def _text_inside_bbox(lines: list[LayoutLine], bbox: tuple[float, float, float, float]) -> str:
    """Return compact text for lines whose centers fall inside a bbox."""

    selected = [line.text for line in lines if _point_inside_bbox(_bbox_center(line.bbox), bbox)]  # 取中心点在区域内的行。
    return compact_inline(" ".join(selected))  # 合并成一段可匹配文本。


def _point_inside_bbox(point: tuple[float, float], bbox: tuple[float, float, float, float]) -> bool:
    """Return True when point lies inside bbox."""

    return bbox[0] <= point[0] <= bbox[2] and bbox[1] <= point[1] <= bbox[3]  # 普通矩形包含判断。


def _bbox_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    """Return bbox center point."""

    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)  # 平均左右和上下坐标。


def _bbox_overlap_ratio(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    """Return overlap area divided by the smaller bbox area."""

    left = max(first[0], second[0])  # 交集左边界。
    top = max(first[1], second[1])  # 交集上边界。
    right = min(first[2], second[2])  # 交集右边界。
    bottom = min(first[3], second[3])  # 交集下边界。
    if right <= left or bottom <= top:  # 没有交集。
        return 0.0  # 返回零。
    intersection = (right - left) * (bottom - top)  # 交集面积。
    first_area = max(1.0, (first[2] - first[0]) * (first[3] - first[1]))  # 第一区域面积。
    second_area = max(1.0, (second[2] - second[0]) * (second[3] - second[1]))  # 第二区域面积。
    return intersection / min(first_area, second_area)  # 相对较小区域计算覆盖率。


def _expanded_bbox(bbox: tuple[float, float, float, float], padding: float) -> tuple[float, float, float, float]:
    """Return bbox expanded by a small padding."""

    return (bbox[0] - padding, bbox[1] - padding, bbox[2] + padding, bbox[3] + padding)  # 四边同时外扩。


def _union_bbox(bboxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    """Return a bbox covering every input bbox."""

    return (  # 组合边界。
        min(min(bbox[0], bbox[2]) for bbox in bboxes),  # 最左坐标。
        min(min(bbox[1], bbox[3]) for bbox in bboxes),  # 最上坐标。
        max(max(bbox[0], bbox[2]) for bbox in bboxes),  # 最右坐标。
        max(max(bbox[1], bbox[3]) for bbox in bboxes),  # 最下坐标。
    )


def _dominant_font(fonts: list[str]) -> str:
    """Return the most common font name from a line."""

    if not fonts:  # 没有字体信息时返回空。
        return ""  # 空字体。
    return max(set(fonts), key=fonts.count)  # 取出现次数最多的字体。
