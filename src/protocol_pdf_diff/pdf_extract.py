"""PDF text extraction helpers.

The extractor requires ``pdfplumber`` so table rows, page-coordinate cleanup,
and table screenshots use one consistent layout source. Scanned image PDFs,
some CAD exports, and badly embedded fonts may still need OCR before this tool
can compare them.
"""

from __future__ import annotations

import re  # 使用正则识别行号边栏、表头和表格值模式。
import base64  # 把表格截图编码成 data URI，HTML 报告可以离线打开。
import io  # 在内存中保存 JPEG 截图，避免生成临时图片文件。
import shutil  # 检测 tesseract 可执行文件是否存在，决定是否启用 OCR。
from pathlib import Path

from .models import ExtractionResult, PageText, TableVisual
from .text_utils import (
    compact_inline,  # 表题过滤需要压缩空白后判断 Figure、X/Y 等误识别候选。
    normalize_line,  # 复用统一空白规整逻辑，保证提取层和比较层口径一致。
    remove_draft_watermark_letter_artifacts,  # 清理混入正文单词的 DRAFT 水印字母残片。
)


class MissingDependencyError(RuntimeError):
    """Raised when the user has not installed project requirements."""


class PdfReadError(RuntimeError):
    """Raised for PDF files that cannot be opened or decoded."""


_WATERMARK_MIN_FONT_SIZE = 40.0  # 大号 DRAFT 水印通常远大于正文；结合字符内容过滤，避免误删大标题。
_WATERMARK_TEXT_CHARS = frozenset("draftDRAFT")  # 只把明确来自 DRAFT 水印且旋转的大字母剔除，保留章节封面标题。
_LINE_NUMBER_MIN_COUNT = 12  # 行号边栏通常有几十个连续数字；少于该数量时不裁边，避免误删正文编号。
_LINE_NUMBER_MIN_RUN = 10  # 需要存在较长连续数字段，才把窄边栏判定为行号栏。
_TABLE_ROW_PREFIX = "表格行:"  # 报告里的表格行标记，方便比较器把表格行当作独立审阅单元。
_TABLE_HEADER_SCAN_ROWS = 12  # pdfplumber 有时把标题/注释放在表格开头，需要在前十余行内寻找表头。
_TABLE_SCREENSHOT_RESOLUTION = 144  # 表格截图使用 2x PDF 点阵，兼顾清晰度和 HTML 体积。
_TABLE_SCREENSHOT_PADDING = 10.0  # 截图在表格 bbox 外保留少量边距，方便看见表题和边框。
_UNSTRUCTURED_TABLE_MIN_CHARS = 160  # 超过该长度且数字密集的正文行，才可能是表格被抽成的一整行。
_UNSTRUCTURED_TABLE_WORDS = frozenset(
    {
        "bandwidth",
        "capacitance",
        "characteristic",
        "coefficient",
        "condition",
        "equalizer",
        "frequency",
        "impedance",
        "maximum",
        "minimum",
        "parameter",
        "receiver",
        "resistance",
        "symbol",
        "termination",
        "transmitter",
        "units",
        "value",
        "voltage",
    }
)  # 这些词组合出现且已有结构化表格行时，原始长行大概率是重复表格噪声。
_DEFAULT_TABLE_HEADERS_BY_WIDTH: dict[int, tuple[str, ...]] = {
    4: ("Parameter", "Symbol", "Value", "Units"),  # OIF COM 参数表常见列宽，续页缺表头时也能结构化。
    5: ("Parameter", "Symbol", "Min", "Max", "Units"),  # 常见电气限制表，保留上下限列含义。
    6: ("Parameter", "Symbol", "Condition", "Min", "Max", "Units"),  # 常见带条件的参数表。
    7: ("Characteristic", "Symbol", "Condition", "Min", "Typ", "Max", "Unit"),  # 常见特性表表头。
}  # 默认表头只在明确没有扫描到表头时使用，不改变外部 API。


def extract_pdf_text(
    pdf_path: str | Path,
    start_page: int | None = None,
    end_page: int | None = None,
) -> ExtractionResult:
    """Extract selectable text from a one-based inclusive PDF page range.

    Args:
        pdf_path: Path to the PDF file.
        start_page: First page to extract, using the page number shown by most
            PDF readers. ``None`` starts at page 1.
        end_page: Last page to extract, inclusive. ``None`` ends at the final
            page of this PDF.

    Returns:
        ExtractionResult with one PageText per page and non-fatal warnings.

    Raises:
        FileNotFoundError: The PDF path does not exist.
        MissingDependencyError: pdfplumber is missing.
        PdfReadError: pdfplumber cannot open the file.
        ValueError: The requested page range is invalid for this PDF.
    """

    path = Path(pdf_path).expanduser().resolve()  # 统一成绝对路径，报告和错误信息都能直接定位源文件。
    if not path.exists():  # 提前检查路径，避免底层 PDF 库给出难懂的打开错误。
        raise FileNotFoundError(f"PDF 文件不存在: {path}")

    try:  # 必须使用 pdfplumber，因为表格截图、坐标过滤和行级表格识别都依赖它。
        return _extract_pdf_text_with_pdfplumber(path, start_page, end_page)
    except ModuleNotFoundError as exc:  # 缺少 pdfplumber 时直接失败，禁止静默退回 pypdf。
        raise MissingDependencyError(
            "缺少依赖 pdfplumber，无法执行表格截图和坐标过滤。"
            "请在项目目录运行: .venv/bin/python -m pip install -r requirements.txt"
        ) from exc


def _extract_pdf_text_with_pdfplumber(
    path: Path,
    start_page: int | None,
    end_page: int | None,
) -> ExtractionResult:
    """Extract layout-aware text and structured table rows with pdfplumber."""

    try:  # 延迟导入使缺失依赖可以被清晰地转成用户可理解的错误。
        import pdfplumber
    except ModuleNotFoundError:  # 外层会转成明确的依赖安装提示，禁止退回低保真提取器。
        raise

    warnings: list[str] = []  # 收集非致命抽取问题，最终写进报告。
    try:  # pdfplumber 负责解析页面坐标、文本和表格。
        pdf = pdfplumber.open(str(path))
    except Exception as exc:  # PDF 解析失败时给出包含文件名的错误。
        raise PdfReadError(f"无法读取 PDF: {path}\n原因: {exc}") from exc

    try:  # 使用上下文式关闭底层文件句柄，避免长批处理时占用文件。
        total_pages = len(pdf.pages)  # 记录源 PDF 总页数，报告页码范围需要它。
        selected_start, selected_end = _resolve_page_range(
            total_pages=total_pages,
            start_page=start_page,
            end_page=end_page,
            pdf_name=path.name,
        )  # 复用统一页码校验，保证不同入口的页码口径一致。
        pages: list[PageText] = []  # 保存每一页清洗后的正文和表格行。
        table_visuals: list[TableVisual] = []  # 保存表格截图和识别摘要，供 HTML 报告显示。
        for index in range(selected_start, selected_end + 1):  # 页码是用户看到的 1-based 范围。
            page = pdf.pages[index - 1]  # pdfplumber 页列表是 0-based，需要减一取页。
            text, page_warnings, page_visuals = _extract_pdfplumber_page_text(page, path.name, index)
            warnings.extend(page_warnings)  # 单页表格或文本抽取失败不应中断整份报告。
            table_visuals.extend(page_visuals)  # 表格截图单独积累，不混入普通正文。
            pages.append(PageText(page_number=index, text=text))  # 保留原始页码供报告定位。
    finally:
        pdf.close()  # 明确关闭 pdfplumber 打开的文件资源。

    return _finalize_extraction_result(
        path=path,
        pages=pages,
        warnings=warnings,
        total_pages=total_pages,
        selected_start=selected_start,
        selected_end=selected_end,
        table_visuals=table_visuals,
    )  # 统一追加空页和低字符数警告。


def _extract_pdfplumber_page_text(
    page: object,
    pdf_name: str,
    page_number: int,
) -> tuple[str, list[str], list[TableVisual]]:
    """Extract one page after removing layout noise and adding table rows."""

    warnings: list[str] = []  # 单页内部的非致命问题独立收集，便于定位页码。
    filtered_page = _filtered_layout_page(page)  # 去掉行号边栏和大号水印后再抽取正文/表格。
    try:
        text = filtered_page.extract_text(x_tolerance=1, y_tolerance=3) or ""
    except Exception as exc:  # 正文抽取失败时仍尝试表格，尽量保留可用信息。
        text = ""
        warnings.append(f"{pdf_name}: 第 {page_number} 页布局文本抽取失败: {exc}")

    text = _clean_extracted_page_text(text)  # 在抽取层过滤 DRAFT、copyright、页眉页脚和 1~49 行号残留。
    if _page_may_contain_table(filtered_page, text):  # 只有疑似表格页才调用较慢的表格识别。
        table_lines, table_visuals, table_warnings = _extract_table_lines_and_visuals(
            filtered_page,
            pdf_name,
            page_number,
        )
        warnings.extend(table_warnings)  # 表格失败不阻塞正文比较。
    else:
        table_lines = []  # 普通正文页无需追加结构化表格行。
        table_visuals = []  # 普通正文页也不生成表格截图。
    return _combine_text_and_table_lines(text, table_lines), warnings, table_visuals


def _clean_extracted_page_text(text: str) -> str:
    """Remove obvious margin and boilerplate noise at the extraction layer."""

    cleaned_lines: list[str] = []  # 按行保留正文，避免整页级正则误删正常段落。
    raw_lines = [normalize_line(raw_line) for raw_line in text.splitlines()]  # 先统一空白，便于识别竖排噪声。
    for line in _drop_vertical_extraction_noise_lines(raw_lines):
        if not line:
            continue
        line = _remove_inline_margin_line_number_run(line)  # 有些 PDF 会把 1~49 行号拼进一行。
        line = remove_draft_watermark_letter_artifacts(line)  # 清理 trRansmitter / a F transmitter 这类水印字母残片。
        line = normalize_line(line)  # 删除行号后再次收紧空白。
        if not line:
            continue
        if _looks_like_extraction_boilerplate(line):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _drop_vertical_extraction_noise_lines(lines: list[str]) -> list[str]:
    """Remove D/R/A/F/T and 1..49 when PDF extraction emits them one per line."""

    kept: list[str] = []  # 保存删除竖排噪声后的行。
    index = 0  # 手动索引便于一次跳过整段竖排噪声。
    while index < len(lines):
        draft_run_length = _vertical_draft_run_length(lines, index)
        if draft_run_length:
            index += draft_run_length
            continue
        line_number_run_length = _vertical_line_number_run_length(lines, index)
        if line_number_run_length:
            index += line_number_run_length
            continue
        kept.append(lines[index])
        index += 1
    return kept


def _vertical_draft_run_length(lines: list[str], start_index: int) -> int:
    """Return 5 when lines at start form a vertical DRAFT watermark."""

    draft_letters = ["D", "R", "A", "F", "T"]  # 竖排 DRAFT 通常被抽成五个独立大写字母。
    end_index = start_index + len(draft_letters)  # 检查窗口刚好覆盖 DRAFT 五行。
    if end_index > len(lines):
        return 0
    candidate = [line.upper() for line in lines[start_index:end_index]]  # 大小写差异不影响水印判断。
    return len(draft_letters) if candidate == draft_letters else 0


def _vertical_line_number_run_length(lines: list[str], start_index: int) -> int:
    """Return the length of a vertical 1..49 margin line-number run."""

    numbers: list[int] = []  # 保存从当前位置开始连续出现的纯数字行。
    index = start_index  # 当前扫描行号。
    while index < len(lines) and re.fullmatch(r"(?:[1-9]|[1-4]\d)", lines[index]):
        numbers.append(int(lines[index]))
        index += 1
    if len(numbers) < 10:
        return 0
    longest = 1  # 纯数字行足够多时检查是否构成连续行号段。
    current = 1  # 当前连续段长度。
    for previous, value in zip(numbers, numbers[1:], strict=False):
        if value == previous + 1:
            current += 1
        else:
            longest = max(longest, current)
            current = 1
    return len(numbers) if max(longest, current) >= 10 else 0


def _remove_inline_margin_line_number_run(line: str) -> str:
    """Remove extracted margin line-number runs such as ``1 2 ... 49``."""

    return re.sub(
        r"(?:^|\s)(?:[1-9]|[1-4]\d)(?:\s+(?:[1-9]|[1-4]\d)){11,}(?=\s|$)",
        " ",
        line,
    )


def _looks_like_extraction_boilerplate(line: str) -> bool:
    """Return True for DRAFT, copyright, and repeated OIF page furniture."""

    candidate = normalize_line(line)  # 单行噪声判断使用统一空白后的文本。
    if not candidate:
        return False
    if _looks_like_margin_line_number_only(candidate):
        return True
    boilerplate_patterns = (
        r"(?i)^draft$",
        r"(?i)\bthis\s+is\s+a\s+draft\s+and\s+not\s+to\s+be\s+shared\b",
        r"(?i)\bthe\s+[“\"]?draft[”\"]?\s+watermark\s+is\s+not\s+to\s+be\s+removed\b",
        r"(?i)\bcopyright\s+©?\s*\d{4}\s+optical\s+internetworking\s+forum\b",
        r"(?i)^optical\s+internetworking\s+forum\s+-\s+clause\s+\d+:",
    )
    return any(re.search(pattern, candidate) for pattern in boilerplate_patterns)


def _looks_like_margin_line_number_only(line: str) -> bool:
    """Return True when a line is mostly the 1~49 margin line-number gutter."""

    numbers = [int(value) for value in re.findall(r"\d+", line)]  # 提取数字后检查是否全在行号范围内。
    if len(numbers) < 12:
        return False
    if min(numbers) < 1 or max(numbers) > 49:
        return False
    remainder = re.sub(r"[\d\s]+", "", line)  # 除数字和空白外仍有内容时不能当纯行号删除。
    if remainder:
        return False
    unique_numbers = sorted(set(numbers))  # 去重后计算最长连续行号段。
    longest = 1  # 至少一个数字时最长段从 1 开始。
    current = 1  # 当前连续段长度。
    for previous, value in zip(unique_numbers, unique_numbers[1:], strict=False):
        if value == previous + 1:
            current += 1
        else:
            longest = max(longest, current)
            current = 1
    return max(longest, current) >= 10


def _filtered_layout_page(page: object) -> object:
    """Return a page view with gutter line numbers and large watermarks removed."""

    left, right = _content_x_bounds_without_line_gutters(page)  # 用坐标识别左右窄边栏里的连续行号。
    try:
        working_page = page.crop((left, 0, right, page.height))
    except Exception:
        working_page = page  # 裁剪失败时保留原页，后续仍可抽取正文。
    return working_page.filter(_keep_non_watermark_object)  # 过滤大号水印字符，避免污染表格单元格。


def _content_x_bounds_without_line_gutters(page: object) -> tuple[float, float]:
    """Detect narrow line-number gutters and return safe horizontal bounds."""

    width = float(getattr(page, "width", 0) or 0)  # 页面宽度用于判断左右边栏比例。
    height = float(getattr(page, "height", 0) or 0)  # 页面高度用于确认行号纵向跨度。
    if width <= 0 or height <= 0:  # 异常页面没有可靠坐标时不裁剪。
        return 0.0, width
    try:
        words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    except Exception:
        return 0.0, width  # 坐标词抽取失败时不做边栏裁剪。

    left_numbers = [
        word
        for word in words
        if _is_gutter_line_number(word, width, height, side="left")
    ]  # 收集左边栏候选行号。
    right_numbers = [
        word
        for word in words
        if _is_gutter_line_number(word, width, height, side="right")
    ]  # 收集右边栏候选行号。
    left = 0.0  # 默认保留页面左边界。
    right = width  # 默认保留页面右边界。
    if _looks_like_line_number_sequence(left_numbers, height):  # 只有连续行号足够明显才裁掉左边栏。
        left = _gutter_inner_bound(left_numbers, "x1", high_side=True) + 5.0
    if _looks_like_line_number_sequence(right_numbers, height):  # 只有连续行号足够明显才裁掉右边栏。
        right = _gutter_inner_bound(right_numbers, "x0", high_side=False) - 5.0
    if right - left < width * 0.55:  # 防御异常检测，避免把正文大面积裁掉。
        return 0.0, width
    return left, right


def _gutter_inner_bound(words: list[dict[str, object]], key: str, *, high_side: bool) -> float:
    """Return a robust gutter boundary while ignoring page-number outliers."""

    values = sorted(float(word[key]) for word in words)  # 对候选坐标排序，便于取分位数而不是极端值。
    if not values:
        return 0.0
    if high_side:
        index = max(0, min(len(values) - 1, int(len(values) * 0.9) - 1))  # 左边栏取 90% 分位，忽略偏右页码。
    else:
        index = max(0, min(len(values) - 1, int(len(values) * 0.1)))  # 右边栏取 10% 分位，忽略偏左页码。
    return values[index]


def _is_gutter_line_number(word: dict[str, object], width: float, height: float, *, side: str) -> bool:
    """Return True for a numeric word that sits in a likely line-number gutter."""

    text = str(word.get("text", "")).strip()  # pdfplumber 词对象里的原始文本。
    if not text.isdigit():  # 行号候选必须是纯数字。
        return False
    value = int(text)  # 转成整数后可以排除页码或异常大数。
    if value < 1 or value > 120:  # 协议行号通常在几十以内，过大数值更可能是正文。
        return False
    top = float(word.get("top", 0) or 0)  # 词的上边界，用于排除页眉页脚页码。
    bottom = float(word.get("bottom", 0) or 0)  # 词的下边界，用于排除页眉页脚页码。
    if top < height * 0.06 or bottom > height * 0.94:  # 页眉页脚数字不参与行号栏判断。
        return False
    if side == "left":  # 左行号栏靠近页面左侧。
        return float(word.get("x1", width) or width) <= width * 0.16
    return float(word.get("x0", 0) or 0) >= width * 0.84  # 右行号栏靠近页面右侧。


def _looks_like_line_number_sequence(words: list[dict[str, object]], height: float) -> bool:
    """Check whether gutter candidates form a real vertical line-number run."""

    if len(words) < _LINE_NUMBER_MIN_COUNT:  # 数量太少时不判定为行号栏。
        return False
    values = sorted({int(str(word["text"]).strip()) for word in words})  # 去重后按数字顺序检查连续性。
    longest_run = _longest_consecutive_run(values)  # 连续行号比零散数字更可信。
    vertical_span = max(float(word["bottom"]) for word in words) - min(float(word["top"]) for word in words)
    return longest_run >= _LINE_NUMBER_MIN_RUN and vertical_span >= height * 0.35


def _longest_consecutive_run(values: list[int]) -> int:
    """Return the length of the longest +1 run in sorted unique integers."""

    if not values:  # 空列表没有连续段。
        return 0
    longest = 1  # 至少有一个数字时，最长段起始为 1。
    current = 1  # 当前连续段长度。
    for previous, value in zip(values, values[1:], strict=False):  # 逐对检查是否相邻。
        if value == previous + 1:
            current += 1
        else:
            longest = max(longest, current)
            current = 1
    return max(longest, current)


def _keep_non_watermark_object(obj: dict[str, object]) -> bool:
    """Filter predicate that removes huge text-watermark characters only."""

    if obj.get("object_type") != "char":  # 非字符对象继续保留，表格线条仍可用于识别表格。
        return True
    size = float(obj.get("size", 0) or 0)  # 字体大小是区分正文和巨大水印的稳定信号。
    text = str(obj.get("text", ""))  # 单字符内容用于确认它确实属于 DRAFT 水印，而不是大号章节标题。
    upright = bool(obj.get("upright", True))  # DRAFT 水印通常是旋转字符；普通大标题一般 upright=True。
    return size < _WATERMARK_MIN_FONT_SIZE or text not in _WATERMARK_TEXT_CHARS or upright


def _extract_table_lines(page: object, pdf_name: str, page_number: int) -> tuple[list[str], list[str]]:
    """Extract tables from one filtered page and format them as review lines."""

    table_lines, _table_visuals, warnings = _extract_table_lines_and_visuals(
        page,
        pdf_name,
        page_number,
    )  # 兼容旧测试和旧调用，只返回文本行和警告。
    return table_lines, warnings


def _extract_table_lines_and_visuals(
    page: object,
    pdf_name: str,
    page_number: int,
) -> tuple[list[str], list[TableVisual], list[str]]:
    """Extract structured table rows and screenshot visual evidence."""

    try:
        table_objects = page.find_tables() or []
    except Exception as exc:
        return [], [], [f"{pdf_name}: 第 {page_number} 页表格定位失败: {exc}"]
    lines: list[str] = []  # 汇总该页所有表格行。
    visuals: list[TableVisual] = []  # 汇总该页所有表格截图和视觉识别摘要。
    warnings: list[str] = []  # 单页表格截图和 OCR 相关的非致命问题。
    for table_number, table in enumerate(table_objects, start=1):
        try:
            rows = table.extract() or []
        except Exception as exc:
            warnings.append(f"{pdf_name}: 第 {page_number} 页第 {table_number} 个表格行抽取失败: {exc}")
            rows = []
        table_lines = _table_lines_from_rows(rows, table_number)  # 每个物理表格先转成候选结构化行，后面会过滤误识别图形。
        visual, visual_warning, preserve_table_lines = _build_table_visual(page, table, table_lines, page_number, table_number)
        if visual_warning:
            warnings.append(f"{pdf_name}: 第 {page_number} 页第 {table_number} 个表格截图生成失败: {visual_warning}")
            lines.extend(table_lines)  # 真实表格即使截图失败，仍保留结构化文本供正文 diff 使用。
            continue
        if visual is not None:
            lines.extend(table_lines)  # 只有通过视觉候选质量门槛的表格行才进入正文 diff，避免曲线图污染报告。
            visuals.append(visual)
        elif preserve_table_lines:
            lines.extend(table_lines)  # 一行无 caption 的弱视觉候选仍可能是真表，文本差异不能被静默丢弃。
    return lines, visuals, warnings


def _build_table_visual(
    page: object,
    table: object,
    table_lines: list[str],
    page_number: int,
    table_number: int,
) -> tuple[TableVisual | None, str, bool]:
    """Build one screenshot-backed table visual record."""

    bbox = tuple(float(value) for value in getattr(table, "bbox", ()) or ())  # pdfplumber Table 提供表格边界框。
    if len(bbox) != 4:
        return None, "未取得可靠表格边界", bool(table_lines)
    title = _table_title_above_bbox(page, bbox)  # 先提取表题，便于在截图前过滤曲线图和坐标轴碎片。
    if _looks_like_false_table_candidate(title, table_lines, bbox):
        return None, "", _should_preserve_filtered_table_lines(title, table_lines)  # 图形不展示；真实弱表格仍保留文本。
    image, padded_bbox, image_status = _table_screenshot_image(page, bbox)  # 生成带橙色边框的表格截图。
    if image is None:
        return None, image_status or "截图为空", bool(table_lines)
    image_data_uri = _image_to_data_uri(image)  # 把截图内嵌到 HTML，便于手机直接查看。
    grid_summary = _opencv_grid_summary(image)  # 用 OpenCV 检测截图内网格线，说明视觉表格证据强弱。
    ocr_text, ocr_status = _ocr_table_image(image)  # 有 tesseract 引擎时做 OCR，否则明确说明跳过。
    if image_status:
        grid_summary = f"{grid_summary}; {image_status}"
    return (
        TableVisual(
            page_number=page_number,
            table_number=table_number,
            title=title,
            bbox=padded_bbox,
            image_data_uri=image_data_uri,
            row_texts=table_lines,
            grid_summary=grid_summary,
            ocr_text=ocr_text,
            ocr_status=ocr_status,
            is_continuation=not bool(title),
        ),
        "",
        False,
    )


def _looks_like_false_table_candidate(
    title: str,
    table_lines: list[str],
    bbox: tuple[float, float, float, float],
) -> bool:
    """Return True when pdfplumber has detected a chart or axis fragment."""

    compact_title = compact_inline(title).casefold()  # 表题归一化后用于识别 Figure、X/Y 轴等非表格标题。
    if re.match(r"^figure\s+\d", compact_title):
        return True  # 曲线图网格经常被误识别成表格，但它不应进入表格行 diff。
    if compact_title in {"f", "a", "r", "il min", "il max"} and _looks_like_chart_legend_table_lines(table_lines):
        return True  # 曲线图图例字母被当成 caption 时，也要过滤。
    if compact_title in {"x", "y"} and not table_lines:
        return True  # 坐标轴标签附近的小网格块不是协议表格。
    if not compact_title and _looks_like_chart_legend_table_lines(table_lines):
        return True  # 曲线图图例会被抽成 F/A/R/IL min 等短行，不能当表格。
    width = max(0.0, bbox[2] - bbox[0])  # 计算候选宽度，过窄对象通常是图形边缘或坐标轴。
    height = max(0.0, bbox[3] - bbox[1])  # 计算候选高度，和宽度一起判断异常长条。
    if not table_lines and (width < 90.0 or height < 90.0):
        return True  # 没有结构化行且尺寸很小的候选没有用户复核价值。
    if len(table_lines) <= 1 and not re.search(r"(?i)\btable\s+\d|表\s*\d|\btable\b", compact_title):
        return True  # 没有表题、行数又很少的对象更可能是图或版面线框。
    return False  # 其余候选保留，避免漏掉没有编号但有多行内容的真实表格。


def _should_preserve_filtered_table_lines(title: str, table_lines: list[str]) -> bool:
    """Return True when filtered visual rows should still enter text diff."""

    if not table_lines:
        return False  # 没有结构化行时，过滤视觉候选不会丢失可比文字。
    compact_title = compact_inline(title).casefold()  # 使用和视觉过滤一致的标题口径。
    if re.match(r"^figure\s+\d", compact_title):
        return False  # Figure 图形内容不进入正文 diff，符合用户“fig 不用对比”的范围。
    if compact_title in {"x", "y", "f", "a", "r", "il min", "il max"}:
        return False  # 坐标轴/图例碎片不是协议表格行。
    if _looks_like_chart_legend_table_lines(table_lines):
        return False  # 只有 F/A/R/IL min 这类图例短行时不保留。
    return True  # 其它弱视觉候选保留表格文本，避免一行真实表被静默吞掉。


def _looks_like_chart_legend_table_lines(table_lines: list[str]) -> bool:
    """Return True when rows look like a chart legend rather than table data."""

    if not table_lines or len(table_lines) > 4:
        return False  # 真实小表可以有四行以上，图例误识别通常很短。
    payload = " ".join(line.removeprefix(_TABLE_ROW_PREFIX).strip() for line in table_lines)  # 去掉内部前缀后判断可见内容。
    if "=" in payload:
        return False  # Header=Value 结构说明这是 pdfplumber 识别到的真实表格行。
    tokens = re.findall(r"[A-Za-z]+(?:\s+[A-Za-z]+)?", payload)  # 提取图例词，例如 IL min、IL max。
    if not tokens:
        return True
    short_or_legend = all(len(token.replace(" ", "")) <= 5 for token in tokens)  # 只有很短标签时才按图例处理。
    return short_or_legend and bool(re.search(r"(?i)\b(?:il\s*min|il\s*max|[far])\b", payload))


def _table_screenshot_image(
    page: object,
    bbox: tuple[float, float, float, float],
) -> tuple[object | None, tuple[float, float, float, float], str]:
    """Render a padded table crop and draw the detected bbox in orange."""

    padded_bbox = _padded_bbox(page, bbox, _TABLE_SCREENSHOT_PADDING)  # 截图外扩一点边距，保留表题和边框上下文。
    try:
        cropped_page = page.crop(padded_bbox)  # pdfplumber 使用 PDF 点坐标裁剪页面。
        image = cropped_page.to_image(resolution=_TABLE_SCREENSHOT_RESOLUTION).original.convert("RGB")
    except Exception as exc:
        return None, padded_bbox, f"截图失败: {exc}"
    try:
        from PIL import ImageDraw

        scale = _TABLE_SCREENSHOT_RESOLUTION / 72.0  # PDF point 到截图像素的比例。
        draw = ImageDraw.Draw(image)  # 在截图上画橙框，标出自动识别的表格网格区域。
        x0 = max(0, int((bbox[0] - padded_bbox[0]) * scale))
        y0 = max(0, int((bbox[1] - padded_bbox[1]) * scale))
        x1 = min(image.width - 1, int((bbox[2] - padded_bbox[0]) * scale))
        y1 = min(image.height - 1, int((bbox[3] - padded_bbox[1]) * scale))
        draw.rectangle([x0, y0, x1, y1], outline=(217, 119, 6), width=4)
    except Exception as exc:
        return image, padded_bbox, f"橙框绘制失败: {exc}"
    return image, padded_bbox, ""


def _padded_bbox(
    page: object,
    bbox: tuple[float, float, float, float],
    padding: float,
) -> tuple[float, float, float, float]:
    """Expand a bbox while staying inside page bounds."""

    width = float(getattr(page, "width", 0) or 0)  # 页面宽度用于限制右边界。
    height = float(getattr(page, "height", 0) or 0)  # 页面高度用于限制下边界。
    page_bbox = tuple(float(value) for value in (getattr(page, "bbox", None) or (0.0, 0.0, width, height)))  # 裁剪页可能有非零父坐标。
    page_left, page_top, page_right, page_bottom = page_bbox  # 使用父页面坐标限制截图范围，避免 crop 越界。
    left = max(page_left, bbox[0] - padding)  # 左侧外扩但不越过当前页面视图。
    top = max(page_top, bbox[1] - max(padding * 2.5, 70.0))  # 顶部留足表题空间，避免截图只露出半截 Table 编号。
    right = min(page_right, bbox[2] + padding) if page_right else bbox[2] + padding
    bottom = min(page_bottom, bbox[3] + padding) if page_bottom else bbox[3] + padding
    return left, top, right, bottom


def _image_to_data_uri(image: object) -> str:
    """Encode a PIL image as a compact JPEG data URI."""

    buffer = io.BytesIO()  # 使用内存缓冲区，避免写临时图片文件。
    image.save(buffer, format="JPEG", quality=82, optimize=True)  # JPEG 体积更适合嵌入 HTML。
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")  # data URI 需要 ASCII base64。
    return f"data:image/jpeg;base64,{encoded}"


def _opencv_grid_summary(image: object) -> str:
    """Summarize visible table-grid evidence with OpenCV when available."""

    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError:
        return "OpenCV 未安装，未执行网格检测"
    try:
        rgb = np.array(image)  # PIL 图像转成 numpy 数组供 OpenCV 处理。
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)  # 灰度图足够检测表格线。
        threshold = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_MEAN_C,
            cv2.THRESH_BINARY_INV,
            15,
            10,
        )
        height, width = threshold.shape  # 截图尺寸决定形态学核的最小长度。
        horizontal = cv2.morphologyEx(
            threshold,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, width // 35), 1)),
            iterations=1,
        )
        vertical = cv2.morphologyEx(
            threshold,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, height // 35))),
            iterations=1,
        )
        horizontal_count = _opencv_contour_count(cv2, horizontal)  # 横线数量反映表格行边界证据。
        vertical_count = _opencv_contour_count(cv2, vertical)  # 竖线数量反映表格列边界证据。
    except Exception as exc:
        return f"OpenCV 网格检测失败: {exc}"
    return f"OpenCV 网格检测: 横线 {horizontal_count} 条，竖线 {vertical_count} 条"


def _opencv_contour_count(cv2_module: object, mask: object) -> int:
    """Count contours in one binary line mask."""

    contours, _hierarchy = cv2_module.findContours(mask, cv2_module.RETR_EXTERNAL, cv2_module.CHAIN_APPROX_SIMPLE)
    return len(contours)


def _ocr_table_image(image: object) -> tuple[str, str]:
    """Run OCR only when both pytesseract and the tesseract binary are available."""

    if shutil.which("tesseract") is None:
        return "", "OCR 未启用：未发现 tesseract；使用截图和 pdfplumber 表格行。"
    try:
        import pytesseract
    except ModuleNotFoundError:
        return "", "OCR 未启用：pytesseract 未安装。"
    try:
        text = pytesseract.image_to_string(image, config="--psm 6")  # psm 6 适合统一块状表格区域。
    except Exception as exc:
        return "", f"OCR 失败: {exc}"
    normalized = "\n".join(line for line in (normalize_line(raw) for raw in text.splitlines()) if line)
    if not normalized:
        return "", "OCR 未识别到可用文本；使用截图和 pdfplumber 表格行。"
    return normalized[:1600], "OCR 已执行。"


def _table_title_above_bbox(page: object, bbox: tuple[float, float, float, float]) -> str:
    """Extract a likely table caption immediately above a table bbox."""

    top = max(0.0, bbox[1] - 90.0)  # 表题可能被页眉和空白挤到更高位置，扩大搜索窗口。
    page_width = float(getattr(page, "width", 0) or bbox[2])  # 用整页宽度找表题，避免 caption 超出表格 bbox 横向范围。
    try:
        caption_page = page.crop((0.0, top, page_width, bbox[1]))  # 表题常居中或跨列，不能只裁表格横向范围。
        caption_text = caption_page.extract_text(x_tolerance=1, y_tolerance=3) or ""
    except Exception:
        return ""
    candidates = [
        line
        for raw_line in caption_text.splitlines()
        if (line := normalize_line(line_without_page_furniture(raw_line)))
    ]  # 去掉页眉页脚式文本后再挑表题，避免 running header 盖过真正 caption。
    candidates.extend(_table_title_candidates_from_words(page, bbox, top))  # extract_text 漏掉 caption 时用词坐标重建行。
    for candidate in reversed(candidates):
        if re.match(r"(?i)^table\s+\d+(?:[-–]\d+)?\b|^表\s*\d+", candidate):
            return _clean_table_caption(candidate)  # 优先使用以 Table 开头的真正 caption，并去掉页边行号。
    for candidate in reversed(candidates):
        if re.match(r"(?i)^\d+\s+table\s+\d+(?:[-–]\d+)?\b", candidate):
            return _clean_table_caption(candidate)  # 有些 PDF 会把左侧行号并到 caption 前面。
    for candidate in reversed(candidates):
        if re.search(r"(?i)\btable\s+\d+(?:[-–]\d+)?\b|表\s*\d+", candidate):
            return _clean_table_caption(candidate)
    return candidates[-1] if candidates else ""


def _clean_table_caption(candidate: str) -> str:
    """Remove margin line numbers accidentally merged into a table caption."""

    caption = normalize_line(candidate)  # 统一空白，便于正则处理。
    caption = re.sub(r"(?i)^\d+\s+(?=table\s+\d)", "", caption)  # 删除 caption 前的页边行号，如 “1 Table 32-1”。
    caption = re.sub(r"(?i)(?<=\D)\s+\d+$", "", caption)  # 删除 caption 后的页边行号，如 “Values 1”。
    return caption


def _table_title_candidates_from_words(
    page: object,
    bbox: tuple[float, float, float, float],
    top: float,
) -> list[str]:
    """Rebuild possible caption lines from word coordinates above a table."""

    try:
        words = page.extract_words(x_tolerance=1, y_tolerance=3) or []  # word 坐标比 extract_text 更容易保留居中 caption。
    except Exception:
        return []
    selected: list[tuple[float, float, str]] = []  # 保存表格上方窗口里的词。
    for word in words:
        word_top = float(word.get("top", 0.0) or 0.0)  # pdfplumber word top 使用页面坐标。
        if top <= word_top <= bbox[1] + 5.0:
            selected.append((word_top, float(word.get("x0", 0.0) or 0.0), str(word.get("text", ""))))  # 只保留 caption 搜索窗口。
    lines: list[list[tuple[float, str]]] = []  # 按 y 坐标聚合同一视觉行。
    for word_top, word_x, text in sorted(selected):
        if not text:
            continue
        if not lines or abs(lines[-1][0][0] - word_top) > 4.0:
            lines.append([])  # y 差超过 4 point 视为新行。
        lines[-1].append((word_x, text))
    candidates: list[str] = []  # 输出重建后的候选 caption。
    for line_words in lines:
        line = normalize_line(" ".join(text for _x, text in sorted(line_words)))  # 同一行按 x 坐标恢复阅读顺序。
        cleaned = line_without_page_furniture(line)  # 过滤页眉页脚。
        if cleaned:
            candidates.append(cleaned)
    return candidates


def line_without_page_furniture(raw_line: str) -> str:
    """Return an empty string for obvious caption-search furniture."""

    line = normalize_line(raw_line)  # 搜索表题时先使用和正文一致的单行清洗。
    if re.search(r"(?i)^implementation\s+agreement\s+oif-cei", line):
        return ""  # 页眉不是表题，不能覆盖 Table 32-x caption。
    if re.search(r"(?i)^optical\s+internetworking\s+forum\b", line):
        return ""  # OIF running header 不是表格标题。
    return line  # 其它行交给表题选择逻辑处理。


def _page_may_contain_table(page: object, text: str) -> bool:
    """Return True when a page has textual or geometric table signals."""

    if re.search(
        r"(?i)\btable\s+\d|parameter\s+symbol|characteristic\s+symbol|表格|表\s*\d",
        text,
    ):  # 常见协议表格标题或表头出现时，应进行结构化表格抽取。
        return True
    line_count = len(getattr(page, "lines", []) or [])  # 表格网格通常包含较多直线对象。
    rect_count = len(getattr(page, "rects", []) or [])  # 有些 PDF 用矩形对象组成表格边框。
    return line_count + rect_count >= 10


def _table_lines_from_rows(rows: list[list[object]], table_number: int) -> list[str]:
    """Convert raw pdfplumber table rows into stable comparison lines."""

    raw_rows = [
        [_table_cell_lines(cell) for cell in row]
        for row in rows
    ]  # 先保留单元格内部换行，后面才能把多参数单元格拆成多条逻辑行。
    raw_rows = [row for row in raw_rows if any(any(cell_lines) for cell_lines in row)]  # 删除全空行，避免报告出现无意义表格行。
    if not raw_rows:  # 空表格没有可比较内容。
        return []

    header_index = _find_table_header_row(raw_rows)  # 在表格前几行扫描表头，处理标题行/空白行插在表头前的情况。
    if header_index is None:
        header = _default_header_for_table(raw_rows)  # 续页缺失表头时，用常见协议表宽恢复列含义。
        data_rows = raw_rows  # 没有真实表头时，所有行都作为数据行输出。
    else:
        header = _clean_table_header(raw_rows[header_index])  # 清洗扫描到的表头，用于 Header=Value 片段。
        data_rows = raw_rows[header_index + 1 :]  # 有真实表头时跳过表头本身，只输出数据行。
    lines: list[str] = []  # 输出给比较器的结构化表格行。
    for row in data_rows:
        for expanded_row in _expand_table_row(row, header):  # 一个物理表格行可能包含多条参数记录。
            line = _format_table_row(expanded_row, header, table_number)
            if line:
                lines.append(line)
    return lines


def _table_cell_lines(cell: object) -> list[str]:
    """Return normalized non-empty physical lines from one table cell."""

    if cell is None:  # pdfplumber 用 None 表示跨列或空单元格。
        return []
    return [
        normalized
        for raw_line in str(cell).splitlines()
        if (normalized := normalize_line(raw_line))
    ]  # 保留单元格内部的有效行，供后续识别上下标和多记录列表。


def _clean_table_cell(cell: object) -> str:
    """Normalize one extracted table cell while preserving protocol values."""

    lines = _table_cell_lines(cell)  # 复用结构化表格路径的单元格行清洗规则。
    return _clean_table_cell_lines(lines)  # 对外保持原 helper 的字符串行为，方便既有测试和调用。


def _clean_table_cell_lines(lines: list[str]) -> str:
    """Normalize pre-split table cell lines into one display cell."""

    if not lines:  # 清洗后没有内容的单元格保持为空。
        return ""
    if _looks_like_symbol_fragments(lines):  # 符号列常被抽成 R / 0 或 f / b，需要合并成 R0、fb。
        return "".join(lines)
    return " / ".join(lines)  # 普通多行说明用斜杠分隔，保留每个子项的可读边界。


def _find_table_header_row(rows: list[list[list[str]]]) -> int | None:
    """Find a likely header row near the top of a pdfplumber table."""

    for index, row in enumerate(rows[:_TABLE_HEADER_SCAN_ROWS]):  # 只扫描表头附近，避免把正文里的 Value/Unit 当表头。
        cleaned_row = [_clean_table_cell_lines(cell_lines) for cell_lines in row]  # 表头判断使用单行字符串更稳定。
        if _looks_like_table_header(cleaned_row):
            return index
    return None


def _clean_table_header(row: list[list[str]]) -> list[str]:
    """Normalize one raw header row into stable display labels."""

    return [_clean_header_label(_clean_table_cell_lines(cell_lines), index) for index, cell_lines in enumerate(row)]


def _default_header_for_table(rows: list[list[list[str]]]) -> list[str]:
    """Infer common protocol table headers when a continuation page has none."""

    widths = [len(row) for row in rows if row]  # pdfplumber 续页通常仍保留正确列数。
    if not widths:
        return []
    width = max(set(widths), key=lambda value: (widths.count(value), value))  # 取出现最多的列宽，避免偶发跨列行干扰。
    return list(_DEFAULT_TABLE_HEADERS_BY_WIDTH.get(width, ()))  # 未知列宽不强行标注，保留原始列顺序。


def _expand_table_row(row: list[list[str]], header: list[str]) -> list[list[str]]:
    """Split a physical table row into logical rows when columns contain lists."""

    if not header:  # 没有列含义时不做列表展开，避免错拆普通多行说明。
        return [[_clean_table_cell_lines(cell_lines) for cell_lines in row]]
    labels = _header_labels_for_row(header, len(row))  # 用当前行宽对齐表头，后续按列类型处理。
    column_values = [
        _logical_cell_values(cell_lines, labels[index])
        for index, cell_lines in enumerate(row)
    ]  # 每列先从物理行转换成逻辑值，例如 R/0/R/d 转成 R0、Rd。
    target_count = _table_row_expansion_count(column_values)  # 判断这一物理行是否包含多条参数记录。
    if target_count <= 1:
        return [[_clean_table_cell_lines(cell_lines) for cell_lines in row]]

    expanded_rows: list[list[str]] = []  # 收集拆分后的逻辑表格行。
    for row_index in range(target_count):
        expanded_rows.append(
            [
                _expanded_column_value(values, row_index, target_count, labels[index])
                for index, values in enumerate(column_values)
            ]
        )  # 每个逻辑行按同一序号从各列取值，缺失单元格保持空字符串。
    return expanded_rows


def _logical_cell_values(lines: list[str], label: str) -> list[str]:
    """Convert physical cell lines into logical row values for one column."""

    if not lines:  # 空单元格参与对齐时保留一个空值。
        return [""]
    if _is_symbol_header(label):  # 符号列要先把上下标碎片合回完整符号。
        return _symbol_lines_to_values(lines)
    if _is_value_header(label):
        return _value_lines_to_values(lines)  # 数值列要剔除 pdfplumber 偶发抽出的单字母伪值。
    return _drop_leading_table_noise_lines(lines)  # 参数列可能被 DRAFT 水印单字母污染，先清掉再对齐。


def _drop_leading_table_noise_lines(lines: list[str]) -> list[str]:
    """Remove obvious watermark fragments that precede a table group label."""

    if (
        len(lines) > 1
        and len(lines[0]) == 1
        and lines[0] in _WATERMARK_TEXT_CHARS
        and _looks_like_group_label(lines[1])
    ):
        return lines[1:]  # 例如 D + Device package model: Class A，D 是水印残片而不是参数。
    return lines


def _is_symbol_header(label: str) -> bool:
    """Return True for headers that normally contain compact symbols."""

    normalized = normalize_line(label).casefold().rstrip(".")  # 大小写和句点不影响列类型判断。
    return normalized in {"symbol", "symbols", "sym", "parameter symbol"}  # 覆盖常见英文符号列写法。


def _is_value_header(label: str) -> bool:
    """Return True for columns that primarily contain numeric values."""

    normalized = normalize_line(label).casefold().rstrip(".")  # 表头大小写和句点不影响列类型。
    return normalized in {"value", "values", "min", "minimum", "typ", "typical", "max", "maximum"}  # 覆盖常见数值列。


def _value_lines_to_values(lines: list[str]) -> list[str]:
    """Clean value-column lines while preserving real numeric/protocol values."""

    return lines  # A/B/C 等等级值可能是合法协议值；只有对齐时确认多出一项才删除伪值。


def _looks_like_table_numeric_value(value: str) -> bool:
    """Return True for compact numeric-ish table values."""

    candidate = normalize_line(value)  # 数值可能带逗号、指数、乘号、正负号或单位前缀。
    return bool(re.search(r"\d", candidate)) and bool(
        re.fullmatch(r"[A-Za-z]*\s*[+-]?(?:\d|[.,×xX+\-/])+[A-Za-z0-9./%Ωµμ-]*", candidate)
        or re.search(r"\d", candidate)
    )


def _symbol_lines_to_values(lines: list[str]) -> list[str]:
    """Merge symbol/subscript fragments into one value per logical table row."""

    values: list[str] = []  # 保存合并后的符号值。
    index = 0  # 手动索引便于查看当前行和下一行是否构成上下标。
    while index < len(lines):
        current = lines[index]  # 当前符号基线文本。
        if (
            index + 1 < len(lines)
            and _should_merge_symbol_fragments(current, lines[index + 1])
        ):
            values.append(current + lines[index + 1])  # 例如 R + 0 合并为 R0。
            index += 2
            continue
        values.append(current)  # 不能确认是上下标时保守保留当前行。
        index += 1
    return values or [""]


def _should_merge_symbol_fragments(current: str, next_value: str) -> bool:
    """Return True when two physical lines form one symbol with a subscript."""

    if not _looks_like_symbol_base(current) or not _looks_like_symbol_subscript(next_value):
        return False
    if re.search(r"\d", current) and re.search(r"[A-Za-z]", next_value):
        return False  # P1 / P2 / P3 是三条完整符号，不是 P1P2 + P3。
    return True


def _looks_like_symbol_base(value: str) -> bool:
    """Return True for the base part of a compact technical symbol."""

    return bool(re.search(r"[A-Za-z_Δγτ\uf067\uf074]", value)) and len(value) <= 12 and " " not in value


def _looks_like_symbol_subscript(value: str) -> bool:
    """Return True for a short subscript/suffix line below a symbol."""

    return bool(re.fullmatch(r"[A-Za-z0-9_+\-*/]+", value)) and len(value) <= 6 and " " not in value


def _table_row_expansion_count(columns: list[list[str]]) -> int:
    """Return the likely logical row count represented by one physical row."""

    counts = [len([value for value in column if value]) for column in columns]  # 空值不应决定展开长度。
    multi_counts = [count for count in counts if count > 1]  # 至少两个多值列对齐时，才有足够证据拆行。
    if len(multi_counts) < 2:
        return 1
    candidates = [count for count in multi_counts if count > 1]  # 候选展开长度来自有多条记录的列。
    best = _choose_table_expansion_count(candidates, columns)  # 平手时优先处理“组标题 + N 行参数”的常见形态。
    if all(abs(count - best) <= 1 for count in multi_counts):  # 允许单位列少一项等常见抽取缺口。
        return best
    return 1


def _choose_table_expansion_count(candidates: list[int], columns: list[list[str]]) -> int:
    """Choose the most plausible expansion count from column line counts."""

    frequencies = {count: candidates.count(count) for count in set(candidates)}  # 统计每个行数由多少列支持。
    max_frequency = max(frequencies.values())  # 最高票数作为主要选择标准。
    tied_counts = sorted(count for count, frequency in frequencies.items() if frequency == max_frequency)  # 找出平手行数。
    if (
        len(tied_counts) > 1
        and columns
        and len([value for value in columns[0] if value]) == tied_counts[-1]
        and _looks_like_group_label(next((value for value in columns[0] if value), ""))
    ):
        return tied_counts[0]  # 第一列多出来的一项是组标题时，应选择较小的真实参数行数。
    return tied_counts[-1]  # 其他平手场景取较大值，尽量保留可审阅记录。


def _expanded_column_value(values: list[str], row_index: int, target_count: int, label: str) -> str:
    """Pick one aligned logical value from a possibly shorter or longer column."""

    non_empty_values = [value for value in values if value]  # 展开时忽略纯空行，避免错位。
    if _is_value_header(label):
        non_empty_values = _drop_value_alignment_noise(non_empty_values, target_count)  # 只在行数对齐需要时剔除孤立伪值。
    if not non_empty_values:
        return ""
    if len(non_empty_values) == target_count:
        return non_empty_values[row_index]  # 标准情况：该列和逻辑行数量一致。
    if len(non_empty_values) == target_count + 1 and _looks_like_group_label(non_empty_values[0]):
        return non_empty_values[row_index + 1]  # 第一项是跨行组名时跳过它，后面才是逐行参数。
    if len(non_empty_values) == target_count - 1:
        return non_empty_values[row_index] if row_index < len(non_empty_values) else ""  # 抽取漏掉末尾单位时保留空值。
    if len(non_empty_values) == 1:
        return non_empty_values[0]  # 单个条件/备注值可复用于该物理行内所有逻辑记录。
    return non_empty_values[row_index] if row_index < len(non_empty_values) else " / ".join(non_empty_values)


def _drop_value_alignment_noise(values: list[str], target_count: int) -> list[str]:
    """Drop a single stray value only when it is the extra item causing misalignment."""

    if len(values) != target_count + 1:
        return values  # 行数已经对齐时，A/B/C 这类单字母值必须保留。
    candidates = [
        index
        for index, value in enumerate(values)
        if value == "T"
        and index > 0
        and index + 1 < len(values)
        and _looks_like_table_numeric_value(values[index - 1])
        and _looks_like_table_numeric_value(values[index + 1])
    ]  # 目前只删除 OIF 中实测到的孤立 T，避免误删 A/B/C 等合法等级值。
    if len(candidates) != 1:
        return values
    noise_index = candidates[0]  # 唯一噪声位置可以安全删除。
    return values[:noise_index] + values[noise_index + 1 :]


def _looks_like_group_label(value: str) -> bool:
    """Return True for a table group heading embedded above row labels."""

    normalized = normalize_line(value)  # 组名通常比参数名更概括，如 Device package model interface parameters。
    if not normalized:
        return False
    words = re.findall(r"[A-Za-z]+", normalized)
    if ":" in normalized and re.search(r"(?i)\b(?:class|device|model|note|package)\b", normalized):
        return True  # 例如 Device package model: Class B (Note 1) 是组标题，不是参数行。
    if len(words) >= 3 and re.search(r"(?i)\b(?:parameters?|characteristics?|requirements?|interface)\b", normalized):
        return True
    return normalized.endswith(":")


def _looks_like_symbol_fragments(lines: list[str]) -> bool:
    """Detect short symbol/subscript fragments such as R + 0 or T_J + RMS."""

    if len(lines) > 4:  # 很多行通常是说明列表，不应强行拼成一个符号。
        return False
    joined = "".join(lines)  # 合并后用于检查是否像紧凑技术符号。
    if not re.search(r"[A-Za-z_Δγτ\uf067\uf074]", joined):  # 没有字母或技术符号时更像数值列表。
        return False
    return all(len(line) <= 12 and " " not in line for line in lines)


def _looks_like_table_header(row: list[str]) -> bool:
    """Return True when a row contains common table column labels."""

    labels = {cell.casefold().rstrip(".") for cell in row if cell}  # 统一大小写并去掉表头句点。
    common_labels = {
        "parameter",
        "characteristic",
        "symbol",
        "condition",
        "date",
        "description",
        "label",
        "value",
        "values",
        "revision",
        "units",
        "unit",
        "min",
        "min",
        "typ",
        "max",
    }  # 常见协议表头词，覆盖 COM 参数表和电气特性表。
    return len(labels & common_labels) >= 2


def _format_table_row(row: list[str], header: list[str], table_number: int) -> str:
    """Format one cleaned table row as a concise, searchable diff unit."""

    cells = _trim_trailing_empty_cells(row)  # 末尾空列没有信息，先去掉。
    if not cells:  # 空行不输出。
        return ""
    if header:
        labels = _header_labels_for_row(header, len(cells))  # 表头长度可能短于数据行，需要补默认列名。
        parts = [
            f"{label}={cleaned_cell}"
            for label, cell in zip(labels, cells, strict=False)
            if (cleaned_cell := _clean_table_field_draft_artifacts(label, cell))
        ]  # 用 Header=Value 形式保留列含义，特别适合表格差异审阅。
    else:
        parts = [
            cleaned_cell
            for index, cell in enumerate(cells)
            if (cleaned_cell := _clean_table_field_draft_artifacts(f"列{index + 1}", cell))
        ]  # 没有表头时仍做字段级水印残片清洗。
    if not parts:  # 所有单元格都为空时跳过。
        return ""
    return f"{_TABLE_ROW_PREFIX} T{table_number} | " + " | ".join(parts)


def _clean_table_field_draft_artifacts(label: str, value: str) -> str:
    """Remove obvious DRAFT letters from one structured table field."""

    cleaned = remove_draft_watermark_letter_artifacts(value)  # 词内水印污染在单元格里也可以安全修正。
    cleaned = normalize_line(cleaned)  # 字段清洗后重新压缩空白，避免留下多余分隔符。
    if not cleaned:
        return ""
    normalized_label = normalize_line(label).casefold().rstrip(".")  # 列名决定单字母是否可能是合法协议值。
    cleaned = re.sub(r"(?i)(?<=\d)×x(?=10)", "×", cleaned)  # 3.2×x10-13 是水印 x 混入科学计数法。
    cleaned = re.sub(r"^[DRAFT](?=(?:Max|Min|Typ|NOTES?|See|Section)\b)", "", cleaned)  # AMax、R/NOTES 类前缀残字。
    if "value" in normalized_label:
        cleaned = re.sub(r"^[DRFT](?=\d)", "", cleaned)  # T4 -> 4，保留合法 Value=A/B/C。
        cleaned = re.sub(r"(?i)^[DRFT](?=(?:No|Yes|True|False)\b)", "", cleaned)  # FNo -> No。
        if "values" in normalized_label and cleaned in {"D", "R", "F", "T"}:
            return ""  # Test 1 values=R 这类孤立值是水印残片。
    if normalized_label in {"min", "minimum", "typ", "typical", "max", "maximum"}:
        if cleaned in {"D", "R", "F", "T"}:
            return ""  # 限值列里的单独 D/R/F/T 是水印残字；Value=A/B/C 等合法等级值仍保留。
    if _is_symbol_header(label):
        if len(cleaned) > 2:
            cleaned = re.sub(r"^[DRAFT](?=[a-zα-ωΑ-Ωρμτγδβ\uf072])", "", cleaned)  # Afmin/Fρx/Fx 这类前缀残字不属于符号本身。
    else:
        cleaned = re.sub(r"^(?:[DRAFT]\s*/\s*)+(?=\S)", "", cleaned)  # A / See Note、R / NOTES 里的首字母是水印残片。
        cleaned = re.sub(r"(?<=\s/)\s*[DRFT]\s*(?=/\s|\s|$)", " ", cleaned)  # 说明单元格内部的 D/R/F/T 孤立片段可删除。
        cleaned = re.sub(r"(?<![A-Za-z0-9_])[DRFT](?![A-Za-z0-9_])", " ", cleaned) if _table_text_field_has_context(cleaned) else cleaned
    cleaned = re.sub(r"\s*/\s*/\s*", " / ", cleaned)  # 删除残字后可能留下重复斜杠，需要收紧。
    cleaned = re.sub(r"[ \t\u00a0]+", " ", cleaned).strip(" /")  # 去掉边缘空白和残留分隔符。
    return cleaned


def _table_text_field_has_context(value: str) -> bool:
    """Return True when a table text field has enough words to drop isolated letters."""

    words = re.findall(r"[A-Za-z]{2,}", value)  # 长说明字段里的孤立残字通常来自 DRAFT 水印。
    return len(words) >= 3


def _header_labels_for_row(header: list[str], cell_count: int) -> list[str]:
    """Build header labels matching the current row width."""

    labels = [_clean_header_label(cell, index) for index, cell in enumerate(header[:cell_count])]  # 清洗已有表头。
    while len(labels) < cell_count:
        labels.append(f"列{len(labels) + 1}")  # 超出表头的列用稳定中文列号补齐。
    return labels


def _clean_header_label(cell: str, index: int) -> str:
    """Return a compact header label safe for Header=Value snippets."""

    label = normalize_line(cell).rstrip(".")  # 去掉表头尾部句点，减少 MIN. / MIN 的伪差异。
    return label or f"列{index + 1}"


def _trim_trailing_empty_cells(row: list[str]) -> list[str]:
    """Drop empty cells at the end of a table row only."""

    end = len(row)  # 从行尾开始寻找最后一个非空单元格。
    while end > 0 and not row[end - 1]:
        end -= 1
    return row[:end]


def _combine_text_and_table_lines(text: str, table_lines: list[str]) -> str:
    """Append structured table rows after normal page text without duplicates."""

    normalized_table_lines = [normalize_line(line) for line in table_lines if normalize_line(line)]  # 表格行也先规整，便于去重和覆盖判断。
    table_token_index = _table_duplicate_token_index(normalized_table_lines)  # 用结构化表格 token 判断原始长表格行是否重复。
    text_lines = [
        line
        for raw_line in text.splitlines()
        if (line := normalize_line(raw_line))
        and not _looks_like_duplicate_unstructured_table_line(line, table_token_index)
    ]  # 正文做轻量空白规整，并删除已由结构化表格行覆盖的超长表格文本块。
    existing = {_table_line_key(line) for line in text_lines}  # 用规范化键避免重复追加完全相同的行。
    combined = list(text_lines)  # 保留正文原有顺序。
    for line in normalized_table_lines:
        key = _table_line_key(line)
        if key not in existing:
            combined.append(line)
            existing.add(key)
    return "\n".join(combined)


def _table_line_key(line: str) -> str:
    """Normalize a line for duplicate detection only."""

    return re.sub(r"\s+", " ", line).casefold().strip()


def _table_duplicate_token_index(table_lines: list[str]) -> set[str]:
    """Build a token index from structured table rows on the same page."""

    tokens: set[str] = set()  # 聚合所有结构化表格 token，用于识别重复的原始表格长行。
    for table_line in table_lines:
        tokens.update(_unstructured_table_tokens(table_line))  # 每条表格行贡献参数名、符号和值等 token。
    return tokens


def _looks_like_duplicate_unstructured_table_line(line: str, table_tokens: set[str]) -> bool:
    """Return True when raw extracted text duplicates structured table rows."""

    if not table_tokens:  # 没有结构化表格时，不能删除正文长行。
        return False
    words = set(re.findall(r"[A-Za-z]+", line.casefold()))  # 只用英文技术词判断，避免误伤中文段落。
    line_tokens = _unstructured_table_tokens(line)  # 计算该长行与结构化表格行的 token 重叠。
    if not line_tokens:
        return False
    overlap = len(line_tokens & table_tokens)  # 重叠越高，越说明这行只是同一张表的原始抽取文本。
    if _looks_like_duplicate_short_table_row(line, line_tokens, overlap, words):
        return True
    if len(line) < _UNSTRUCTURED_TABLE_MIN_CHARS:  # 未命中短表格行规则时，短行更可能是正文或表题。
        return False
    if len(re.findall(r"\d+(?:\.\d+)?", line)) < 6:  # 表格噪声通常包含大量数值、编号或单位。
        return False
    if len(words & _UNSTRUCTURED_TABLE_WORDS) < 2:
        return False
    required_overlap = min(12, max(6, len(line_tokens) // 5))  # 长行需要更多重叠，短表格块至少需要 6 个 token。
    return overlap >= required_overlap


def _looks_like_duplicate_short_table_row(
    line: str,
    line_tokens: set[str],
    overlap: int,
    words: set[str],
) -> bool:
    """Return True for one raw table row already represented structurally."""

    if len(line_tokens) < 4:  # token 太少的短行容易误伤标题或脚注。
        return False
    if line.rstrip().endswith((".", "。", ";", "；")) and len(line) < 100:
        return False  # 短完整句子更可能是正文说明，保守保留。
    overlap_ratio = overlap / len(line_tokens)  # 短行要求绝大多数 token 已出现在结构化表格行里。
    has_value_shape = bool(re.search(r"\d", line)) and bool(
        re.search(r"(?:Ω|—)|\b(?:ohm|ff|pf|ph|ghz|mhz|ui|mv|v|db|ns/mm|1/mm)\b", line, flags=re.I)
    )  # 同时出现数字和单位/破折号，才更像表格值行。
    has_table_word = bool(words & _UNSTRUCTURED_TABLE_WORDS)  # 参数类词给短行提供额外语义证据。
    return overlap_ratio >= 0.8 and has_value_shape and has_table_word


def _unstructured_table_tokens(value: str) -> set[str]:
    """Tokenize table text for duplicate detection, keeping numbers meaningful."""

    lowered = value.casefold().replace("µ", "u").replace("μ", "u")  # 单位符号归一化后，数值 token 更容易重合。
    return {
        token
        for token in re.findall(r"[a-z]+[a-z0-9]*|[+-]?\d+(?:\.\d+)?", lowered)
        if len(token) > 1
    }  # 单字符 token 噪声较多，删除后仍保留 R0、46.25 等关键值。


def _finalize_extraction_result(
    *,
    path: Path,
    pages: list[PageText],
    warnings: list[str],
    total_pages: int,
    selected_start: int,
    selected_end: int,
    table_visuals: list[TableVisual] | None = None,
) -> ExtractionResult:
    """Add common extraction warnings and build the final result object."""

    empty_pages = [page.page_number for page in pages if not page.text.strip()]  # 找出完全没有可抽取文字的页。
    extracted_chars = sum(len(page.text.strip()) for page in pages)
    if empty_pages:
        preview = ", ".join(str(page) for page in empty_pages[:12])
        suffix = "..." if len(empty_pages) > 12 else ""
        warnings.append(
            f"{path.name}: {len(empty_pages)} 页没有抽取到文字"
            f"（页码: {preview}{suffix}）。如果原文是扫描件，请先 OCR。"
        )
    if extracted_chars < 100:
        warnings.append(
            f"{path.name}: 总共只抽取到 {extracted_chars} 个字符，"
            "结果可能不可靠；请确认 PDF 不是图片扫描版。"
        )

    return ExtractionResult(
        pdf_path=path,
        pages=pages,
        warnings=warnings,
        total_pages=total_pages,
        selected_start_page=selected_start,
        selected_end_page=selected_end,
        table_visuals=list(table_visuals or []),
    )


def _resolve_page_range(
    total_pages: int,
    start_page: int | None,
    end_page: int | None,
    pdf_name: str,
) -> tuple[int, int]:
    """Validate and normalize a user-facing one-based page range.

    The extractor keeps original page numbers instead of renumbering the
    selected slice, so a report for pages 30-45 still points to pages 30-45 in
    the source PDF. Empty PDFs are rejected because there is no meaningful range
    to compare.
    """

    if total_pages <= 0:
        raise ValueError(f"{pdf_name}: PDF 没有可读取页。")
    if start_page is not None and start_page < 1:
        raise ValueError(f"{pdf_name}: 起始页必须大于等于 1，当前为 {start_page}。")
    if end_page is not None and end_page < 1:
        raise ValueError(f"{pdf_name}: 终止页必须大于等于 1，当前为 {end_page}。")

    selected_start = 1 if start_page is None else start_page
    selected_end = total_pages if end_page is None else end_page
    if selected_start > total_pages:
        raise ValueError(
            f"{pdf_name}: 起始页 {selected_start} 超出 PDF 总页数 {total_pages}。"
        )
    if selected_end > total_pages:
        raise ValueError(
            f"{pdf_name}: 终止页 {selected_end} 超出 PDF 总页数 {total_pages}。"
        )
    if selected_start > selected_end:
        raise ValueError(
            f"{pdf_name}: 页码范围无效，起始页 {selected_start} 不能大于终止页 {selected_end}。"
        )
    return selected_start, selected_end
