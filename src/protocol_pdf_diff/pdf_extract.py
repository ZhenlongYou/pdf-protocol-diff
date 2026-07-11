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
_TABLE_CAPTION_RE = re.compile(r"(?i)\btable\s+\d+(?:[-–]\d+)?\b|表\s*\d+")  # 识别真正表题，避免把图题当表格。
_FIGURE_CAPTION_RE = re.compile(r"(?i)\b(?:figure|fig\.)\s+\d+(?:[-–.]\d+)?\b|图\s*\d+")  # 识别图题/图片块，按用户要求不做图片对比。
_TABLE_HEADER_SCAN_ROWS = 12  # pdfplumber 有时把标题/注释放在表格开头，需要在前十余行内寻找表头。
_TABLE_SCREENSHOT_RESOLUTION = 144  # 表格截图使用 2x PDF 点阵，兼顾清晰度和 HTML 体积。
_TABLE_SCREENSHOT_PADDING = 10.0  # 截图在表格 bbox 外保留少量边距，方便看见表题和边框。
_UNSTRUCTURED_TABLE_MIN_CHARS = 160  # 超过该长度且数字密集的正文行，才可能是表格被抽成的一整行。
_PCIE_MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|November|December"
)  # PCIe 规范页脚里的英文月份集合，用于限定日期噪声。
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
    lines_without_vertical_noise = _drop_vertical_extraction_noise_lines(raw_lines)  # 先删除竖排 DRAFT/页边行号。
    pcie_furniture_indexes = _pcie_page_furniture_line_indexes(lines_without_vertical_noise)  # 只删除同一页眉簇里的 PCIe 版本/日期。
    for index, line in enumerate(lines_without_vertical_noise):
        if not line:
            continue
        if index in pcie_furniture_indexes:
            continue
        line = _remove_inline_margin_line_number_run(line)  # 有些 PDF 会把 1~49 行号拼进一行。
        line = remove_draft_watermark_letter_artifacts(line)  # 清理 trRansmitter / a F transmitter 这类水印字母残片。
        line = normalize_line(line)  # 删除行号后再次收紧空白。
        if not line:
            continue
        if _looks_like_extraction_boilerplate(line):
            continue
        if _looks_like_figure_or_image_text_block(line):
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
    """Return True for DRAFT, copyright, and repeated page furniture."""

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
        r"(?i)^PCI\s+Express\s+Architecture\s+PHY\s+Test\s+Specification\s*\|\s*\d+$",
    )
    return any(re.search(pattern, candidate) for pattern in boilerplate_patterns)


def _pcie_page_furniture_line_indexes(lines: list[str]) -> set[int]:
    """Return indexes belonging to a PCIe running header/footer cluster."""

    indexes: set[int] = set()  # 保存需要整行删除的页眉页脚行号。
    for index, line in enumerate(lines):
        if not re.fullmatch(r"(?i)PCI\s+Express\s+Architecture\s+PHY\s+Test\s+Specification\s*\|\s*\d+", line):
            continue
        indexes.add(index)  # running header 本身一定是页眉页脚。
        for neighbor in range(max(0, index - 2), min(len(lines), index + 4)):
            if neighbor == index:
                continue
            candidate = lines[neighbor]
            if re.fullmatch(r"(?i)Revision\s+\d+(?:\.\d+)*(?:\s*,\s*Version\s+\d+(?:\.\d+)*)?", candidate):
                indexes.add(neighbor)  # 只有贴近 running header 的版本行才当页脚。
            elif re.fullmatch(rf"(?i)(?:{_PCIE_MONTH_PATTERN})\s+\d{{1,2}},\s+\d{{4}}", candidate):
                indexes.add(neighbor)  # 只有贴近 running header 的日期行才当页脚。
    return indexes


def _looks_like_figure_or_image_text_block(line: str) -> bool:
    """Return True for standalone figure captions, plot text, or image OCR blocks."""

    candidate = normalize_line(line)  # 使用清洗后的单行文本，避免 PDF 抽取空格干扰规则。
    if not candidate:
        return False
    if _starts_with_figure_sentence_prose(candidate):
        return False  # `Figure 32-2 shows ...` 是正文句子，即使提到插损/频率也必须保留。
    if _looks_like_numeric_axis_tick_line(candidate):
        return True  # 纯坐标轴刻度行不是协议正文。
    if _looks_like_axis_only_visual_block(candidate):
        return True  # 没有 Figure 标题但只有坐标轴/曲线标签的视觉碎片也不参与比较。
    if _looks_like_plot_or_formula_block(candidate):
        return True  # 密集坐标轴/公式块即使没有 Figure 标题，也属于图片/图形抽取残片。
    if _looks_like_figure_caption(candidate):
        return True  # 以 Figure/Fig./图 开头的块属于图片证据，用户要求不比较。
    if not _FIGURE_CAPTION_RE.search(candidate):
        return False  # 没有图题引用时，不能按图片块删除普通正文。
    if len(candidate) < 120:
        return False  # 短句里提到 Figure 可能是正文引用，必须保留。
    return _looks_like_plot_or_formula_block(candidate)  # 长图块需同时具备坐标轴/公式形态才删除。


def _looks_like_axis_only_visual_block(value: str) -> bool:
    """Return True for short plot-axis fragments with no sentence content."""

    candidate = normalize_line(value)  # 统一空白后检查坐标轴碎片形态。
    if re.search(r"(?i)\b(?:shall|should|specified|measured|computed|requirements?)\b", candidate):
        return False  # 含规范动词的句子可能是真正文段，不能只因有图轴词删除。
    if _looks_like_short_plot_label(candidate):
        return True  # IL min / Frequency (GHz) 等短轴标签不是正文。
    if re.fullmatch(r"(?i)(?:amplitude|frequency|loss|jitter)(?:\s+[A-Z]){1,4}", candidate):
        return True  # `Amplitude X` 这类短标签是图轴文字，不是协议正文。
    x_count = len(re.findall(r"\bX\b", candidate))  # 曲线图抽取常把坐标交叉或标记输出为多个 X。
    number_count = len(re.findall(r"\d+(?:\.\d+)?", candidate))  # 坐标轴碎片通常也含多个刻度数字。
    axis_words = len(re.findall(r"(?i)\b(?:amplitude|frequency|loss|jitter|ui|ghz|db|pp)\b", candidate))
    return x_count >= 2 and number_count >= 3 and axis_words >= 2


def _looks_like_numeric_axis_tick_line(value: str) -> bool:
    """Return True for long chart-axis tick sequences such as ``0 5 10 ...``."""

    tokens = normalize_line(value).split()  # 坐标轴刻度通常被抽成一串空格分隔数字。
    if len(tokens) < 8:
        return False
    return all(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", token) for token in tokens)


def _looks_like_short_plot_label(value: str) -> bool:
    """Return True for compact chart labels that are not standalone prose."""

    candidate = normalize_line(value)  # 短标签需要严格匹配，避免误删正文句子。
    if re.fullmatch(r"(?i)(?:frequency|amplitude|loss|jitter)\s*\([^)]+\)", candidate):
        return True  # `Frequency (GHz)` 这类裸坐标轴标题不能变成章节标题。
    if re.fullmatch(r"(?i)(?:min|max|typ)", candidate):
        return True  # 图形/公式拆行后常留下单独的 min/max/typ，不是正文句子。
    if re.fullmatch(r"(?i)il\s+(?:min|max)(?:\s*/\s*il\s+(?:min|max))?(?:\s+f\s*[×x*]\s*\d+){0,2}", candidate):
        return True
    if re.match(r"(?i)^frequency\s*\([^)]+\)(?:\s+\(\d+(?:[-–]\d+)?\))?(?:\s+\)bd\(|\s+ssoL)", candidate):
        return True
    return False


def _looks_like_plot_or_formula_block(value: str) -> bool:
    """Return True for dense chart/formula extraction around a figure."""

    lowered = value.casefold()  # 图形判断只需要大小写无关的英文锚点。
    if _looks_like_il_limit_formula_fragment(value):
        return True  # 图中的 IL min/IL max 分段公式不是正文段落，不参与差异比较。
    if _looks_like_symbolic_formula_fragment(value):
        return True  # 只有符号、公式字形和少量 token 的行通常是图片公式残片。
    if re.search(r"(?i)\b(?:shows?|illustrates?|depicts?|shall|should|specified|measured|computed|requirements?)\b", value):
        if not re.search(r"(?i)\b(?:il\s*min|il\s*max|frequency\s*\(|\)bd\(|ssol|insertion\s+loss)\b", value):
            return False  # 普通句首 Figure 正文不能只因有数值而被当作图片块。
    number_count = len(re.findall(r"\d+(?:\.\d+)?", value))  # 图形坐标轴和公式块通常数字密度很高。
    formula_mark_count = len(re.findall(r"[∑σ√≤≥]|(?:--+)", value))  # PDF 公式字符是图片/公式块强信号。
    axis_words = len(
        re.findall(
            r"\b(?:frequency|loss|db|ghz|axis|il\s*min|il\s*max|return\s+loss|insertion\s+loss)\b",
            lowered,
        )
    )  # 坐标轴和曲线标签能区分图形块与普通正文。
    return number_count >= 8 and (formula_mark_count >= 2 or axis_words >= 2)


def _looks_like_symbolic_formula_fragment(value: str) -> bool:
    """Return True for standalone equation glyph fragments with little prose."""

    candidate = normalize_line(value)  # 公式残片常被拆成短行，先规整空白。
    if re.search(r"(?i)\b(?:parameter|characteristic|symbol|condition|value|values|units?|min|max|typ)=", candidate):
        return False  # 结构化表格字段使用等号，但不是图片公式残片。
    word_tokens = re.findall(r"[A-Za-z]{2,}", candidate)  # 正常正文通常有多个英文单词。
    number_count = len(re.findall(r"\d+(?:\.\d+)?", candidate))
    if re.search(r"=", candidate) and number_count >= 2 and len(word_tokens) <= 6:
        return True  # `SNDR = ...`、`N - 1 ... = 0` 这类拆行公式没有正文语义。
    formula_mark_count = len(re.findall(r"[∑σ√≤≥]|(?:--+)", candidate))
    if formula_mark_count < 2:
        return False
    if number_count >= 2 and len(word_tokens) <= 5:
        return True
    return len(candidate) <= 90 and len(word_tokens) <= 3


def _looks_like_il_limit_formula_fragment(value: str) -> bool:
    """Return True for split IL-limit equations emitted from chart/figure text."""

    candidate = normalize_line(value)  # 公式残片在不同 PDF 字体里空格差异很大，先统一成单行。
    if re.search(r"(?i)\bil\s*(?:min|max)\s*=", candidate):
        return True  # `IL min = ...` 是图形/公式抽取残片的强信号。
    has_frequency_math = bool(re.search(r"(?i)\bf\b|\bGHz\b", candidate))  # f/GHz 锚定频率公式上下文。
    has_formula_symbol = bool(re.search(r"[≤≥<>]|--+", candidate))  # 私有字体符号和分数线锚定公式残片。
    return len(candidate) <= 90 and has_frequency_math and has_formula_symbol


def _starts_with_figure_sentence_prose(value: str) -> bool:
    """Return True for real prose sentences that begin with a Figure reference."""

    candidate = _strip_caption_line_noise(value)  # 复用图题前置行号清理逻辑。
    match = re.match(r"(?i)^(?:figure|fig\.)\s+\d+(?:[-–.]\d+)?\b(?P<tail>.*)$", candidate)
    return bool(match and _figure_caption_tail_starts_prose(match.group("tail")))


def _looks_like_figure_caption(value: str) -> bool:
    """Return True when a line is a standalone figure/image caption."""

    candidate = _strip_caption_line_noise(value)  # 先去掉页边行号，避免 `1 Figure 32-2` 漏检。
    match = re.match(r"(?i)^(?:figure|fig\.)\s+\d+(?:[-–.]\d+)?\b(?P<tail>.*)$", candidate)
    if match:
        return not _figure_caption_tail_starts_prose(match.group("tail"))
    chinese_match = re.match(r"^图\s*\d+(?P<tail>.*)$", candidate)
    if chinese_match:
        return not re.match(r"^\s*(?:显示|说明|描述|定义)", chinese_match.group("tail"))
    return False


def _figure_caption_tail_starts_prose(tail: str) -> bool:
    """Return True when text after ``Figure N`` is normal sentence prose."""

    cleaned_tail = tail.lstrip(" .:-–—").strip()  # 去掉图题常用分隔符后看第一个词。
    return bool(
        re.match(
            r"(?i)^(?:shows?|illustrates?|depicts?|describes?|defines?|specifies?|contains?|lists?|is|are|shall|should|must|may|can)\b",
            cleaned_tail,
        )
    )



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
    return not (
        size >= _WATERMARK_MIN_FONT_SIZE
        and text in _WATERMARK_TEXT_CHARS
        and _is_rotated_text_object(obj)
    )  # pdfplumber 的 upright 标志不能代表视觉旋转；真实 OIF 水印需以变换矩阵判断。


def _is_rotated_text_object(obj: dict[str, object]) -> bool:
    """Return True when a PDF text object's transform has material rotation."""

    matrix = tuple(obj.get("matrix", ()) or ())  # PDF 文本矩阵前四项包含缩放、倾斜和旋转信息。
    if len(matrix) >= 4:
        try:
            a, b, c, d = (float(value) for value in matrix[:4])
        except (TypeError, ValueError):
            return not bool(obj.get("upright", True))  # 异常矩阵退回旧标志，保持兼容性。
        axis_scale = max(abs(a), abs(d), 1.0)  # 正常横排文字的 b/c 接近 0，a/d 表示主轴缩放。
        return max(abs(b), abs(c)) >= axis_scale * 0.15  # 15% 足以覆盖 OIF 约 45° 水印并保留正常大标题。
    return not bool(obj.get("upright", True))  # 旧测试/手工对象没有矩阵时沿用 upright 语义。


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
        bbox = _table_bbox(table)  # 先取边界框，用表题和位置判断它是不是真表格。
        try:
            rows = table.extract() or []
        except Exception as exc:
            warnings.append(f"{pdf_name}: 第 {page_number} 页第 {table_number} 个表格行抽取失败: {exc}")
            rows = []
        table_lines = _table_lines_from_rows(rows, table_number)  # 每个物理表格都转成稳定文本行。
        title = _table_title_above_bbox(page, bbox) if bbox else ""  # 表题用于过滤图形误检和生成截图标题。
        if _should_skip_detected_table(title, table_lines):
            continue  # Figure/plot 或空伪表格不进入正文 diff，也不进入表格截图区。
        lines.extend(table_lines)  # 表格行保留在抽取文本中，后续有截图表格区时正文 diff 会自动去重隐藏。
        visual, visual_warning = _build_table_visual(
            page,
            table,
            table_lines,
            page_number,
            table_number,
            title=title,
        )  # 只为通过过滤的表格生成截图证据。
        if visual_warning:
            warnings.append(f"{pdf_name}: 第 {page_number} 页第 {table_number} 个表格截图生成失败: {visual_warning}")
        if visual is not None:
            visuals.append(visual)
    return lines, visuals, warnings


def _table_bbox(table: object) -> tuple[float, float, float, float] | None:
    """Return a normalized table bbox, or None when pdfplumber did not provide one."""

    bbox = tuple(float(value) for value in getattr(table, "bbox", ()) or ())  # pdfplumber 的 bbox 是表格定位的主证据。
    return bbox if len(bbox) == 4 else None  # 无四元组坐标时不能安全裁剪或按表题过滤。


def _should_skip_detected_table(title: str, table_lines: list[str]) -> bool:
    """Return True when a pdfplumber table object is actually a figure/noise region."""

    cleaned_title = normalize_line(title)  # 统一空白后判断标题类型，避免行号残留影响规则。
    if table_lines and _table_lines_are_visual_only(table_lines):
        return True  # 即使没有 bbox，纯坐标轴/公式碎片也不能进入正文 diff 或表格截图区。
    if _looks_like_figure_caption(cleaned_title):
        return True  # 用户明确不需要图片/图形对比，Figure 误检必须整块跳过。
    if _looks_like_non_table_caption(cleaned_title) and not table_lines:
        return True  # 页眉、单字母坐标轴等空候选没有表格证据，直接丢弃。
    if _table_lines_are_single_column_note_box(table_lines) and not _looks_like_table_caption(cleaned_title):
        return True  # 无表题的一列 Note/说明框不是结构化表格，避免把图片/文本框当表格对比。
    if not table_lines and not _looks_like_table_caption(cleaned_title) and not _looks_like_table_context_caption(cleaned_title):
        return True  # 没有结构化行也没有表格语义时，通常是 OpenCV/pdfplumber 误检。
    return False  # 其余候选保守保留，确保真实表格截图不会被误删。


def _table_lines_are_single_column_note_box(table_lines: list[str]) -> bool:
    """Return True for one-column note/text boxes misdetected as tables."""

    payloads = [_single_value_table_payload(line) for line in table_lines]  # 提取 Value= 后的可读文本。
    if not payloads or any(payload is None for payload in payloads):
        return False  # 只处理全部都是单个 Value 单元格的候选，避免误删多列表格。
    joined = " ".join(payload for payload in payloads if payload)  # 汇总整块说明文本，判断是否是 Note 框。
    return bool(re.search(r"(?i)^\s*(?:note|notes)\s*[:.]", joined))  # 只跳过明确 Note/Notes 开头的说明框。


def _single_value_table_payload(line: str) -> str | None:
    """Return the payload when one table row has only a ``Value=`` cell."""

    text = normalize_line(line)  # 使用抽取层统一空白规则，兼容 pdfplumber 的单元格换行。
    if text.startswith(_TABLE_ROW_PREFIX):
        text = text[len(_TABLE_ROW_PREFIX) :].strip()  # 去掉内部“表格行:”前缀。
    text = re.sub(r"^T\d+\s*\|\s*", "", text, flags=re.I)  # 去掉物理表编号，避免 T1/T2 影响判断。
    cells = [cell.strip() for cell in text.split("|") if cell.strip()]  # 单列误检只有一个有效单元。
    if len(cells) != 1:
        return None
    cell = cells[0]
    if not re.match(r"(?i)^value\s*=", cell):
        return None
    return normalize_line(cell.split("=", 1)[1])  # 返回说明框中的实际文本，供 Note 规则判断。


def _table_lines_are_visual_only(table_lines: list[str]) -> bool:
    """Return True when every extracted table row is actually figure/axis text."""

    payloads = [
        payload
        for line in table_lines
        if (payload := _table_line_visual_payload(line))
    ]  # 去掉内部表格前缀后，只检查有内容的行。
    return bool(payloads) and all(_looks_like_figure_or_image_text_block(payload) for payload in payloads)


def _table_line_visual_payload(line: str) -> str:
    """Strip the internal table prefix so visual-noise rules see the real row text."""

    text = normalize_line(line)  # 表格行来自 pdfplumber，先统一空白再剥离内部前缀。
    if text.startswith(_TABLE_ROW_PREFIX):
        text = text[len(_TABLE_ROW_PREFIX) :].strip()  # 删除“表格行:”标记，避免影响图轴规则。
    text = re.sub(r"^T\d+\s*\|\s*", "", text, flags=re.I)  # 删除物理表编号，只保留单元格内容。
    return text.strip()


def _build_table_visual(
    page: object,
    table: object,
    table_lines: list[str],
    page_number: int,
    table_number: int,
    *,
    title: str = "",
) -> tuple[TableVisual | None, str]:
    """Build one screenshot-backed table visual record."""

    bbox = _table_bbox(table)  # 复用统一 bbox 解析，避免过滤和截图路径口径不一致。
    if bbox is None:
        return None, "未取得可靠表格边界"
    image, padded_bbox, image_status = _table_screenshot_image(page, bbox)  # 生成带橙色边框的表格截图。
    if image is None:
        return None, image_status or "截图为空"
    title = title or _table_title_above_bbox(page, bbox)  # 调用方通常已抽过表题；兜底再取一次。
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
    )


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
    top = max(page_top, bbox[1] - padding * 2.5)  # 顶部多留一点空间，尽量包含表题。
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

    page_bbox = tuple(float(value) for value in (getattr(page, "bbox", None) or (0.0, 0.0, page.width, page.height)))  # 当前页面/裁剪页边界。
    page_left, _page_top, page_right, _page_bottom = page_bbox  # 只需要水平边界扩大表题搜索范围。
    left = max(page_left, bbox[0] - 90.0)  # 表题有时比表格本体更宽，左侧多取一点。
    right = min(page_right, bbox[2] + 90.0) if page_right else bbox[2] + 90.0  # 右侧同样外扩，避免漏掉跨列表题。
    top = max(0.0, bbox[1] - 90.0)  # 表题通常在表格上方；90pt 能覆盖多行题名和 line-number 残留。
    try:
        caption_page = page.crop((left, top, right, bbox[1]))
        caption_text = caption_page.extract_text(x_tolerance=1, y_tolerance=3) or ""
    except Exception:
        return ""
    candidates = [
        _strip_caption_line_noise(line)
        for raw_line in caption_text.splitlines()
        if (line := normalize_line(raw_line))
    ]  # 表题候选先去掉页边行号和尾部孤立行号。
    candidates = [candidate for candidate in candidates if candidate and not _looks_like_non_table_caption(candidate)]
    for candidate in reversed(candidates):
        if _looks_like_table_caption(candidate):
            return candidate
    for candidate in reversed(candidates):
        if _looks_like_figure_caption(candidate):
            return candidate  # 返回 Figure 题名供上游过滤掉该候选。
    for candidate in reversed(candidates):
        if _looks_like_table_context_caption(candidate):
            return candidate  # Revision history 这类无编号表，用上下文表述作为标题。
    return ""


def _strip_caption_line_noise(value: str) -> str:
    """Remove margin line numbers that cling to table/figure captions."""

    cleaned = normalize_line(value)  # 表题清洗先做统一空白，后续正则更稳定。
    cleaned = re.sub(r"^\d{1,3}\s+(?=(?:table|figure|fig\.|表|图)\b)", "", cleaned, flags=re.I)  # 删除 `1 Table ...` 前缀行号。
    if _TABLE_CAPTION_RE.search(cleaned) or _FIGURE_CAPTION_RE.search(cleaned):
        cleaned = re.sub(r"\s+\d{1,3}$", "", cleaned)  # 删除 `Table ... 1` 这类尾部行号，但保留题名主体。
    return normalize_line(cleaned)


def _looks_like_table_caption(value: str) -> bool:
    """Return True when a caption explicitly names a table."""

    return bool(_TABLE_CAPTION_RE.search(normalize_line(value)))  # 复用统一表题正则，覆盖英文 Table 和中文表。


def _looks_like_table_context_caption(value: str) -> bool:
    """Return True for unnumbered text that clearly introduces a table."""

    candidate = normalize_line(value).casefold()  # 无编号表只能靠上下文词判断。
    return bool(re.search(r"\btable\s+(?:below|following)|\bin\s+the\s+table\s+below\b|下表", candidate))


def _looks_like_non_table_caption(value: str) -> bool:
    """Return True for page furniture or tiny axis labels that should not title a table."""

    candidate = normalize_line(value)  # 统一空白，避免页眉里的换行影响判断。
    if not candidate:
        return True
    if candidate.isdigit():
        return True
    if re.fullmatch(r"[A-Za-z]", candidate) and candidate.upper() in _WATERMARK_TEXT_CHARS | frozenset({"X", "Y"}):
        return True  # 单个 D/R/A/F/T 或 X/Y 更可能是水印/坐标轴，不是表题。
    if re.search(r"(?i)^implementation\s+agreement\s+oif-cei", candidate):
        return True  # OIF 页眉不应成为旧版 Table 32-7 的标题。
    if _looks_like_extraction_boilerplate(candidate):
        return True
    return False


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
    return _merge_wrapped_table_row_continuations(lines)


def _merge_wrapped_table_row_continuations(lines: list[str]) -> list[str]:
    """Merge pdfplumber rows that are really wrapped text inside one table row."""

    merged: list[str] = []  # 保存合并后的结构化表格行。
    for line in lines:
        if merged and _is_wrapped_table_row_continuation(merged[-1], line):
            merged[-1] = _merge_table_row_continuation(merged[-1], line)  # 例如 T_J + 4.3u03 合成 T_J4.3u03。
            continue
        merged.append(line)  # 非续行保持原顺序。
    return merged


def _is_wrapped_table_row_continuation(previous_line: str, current_line: str) -> bool:
    """Return True when ``current_line`` is a continuation of ``previous_line``."""

    previous_fields = _table_line_field_map(previous_line)  # 上一行字段用于判断是否已有完整数值。
    current_fields = _table_line_field_map(current_line)  # 当前行字段用于判断是否只是续写描述/符号。
    description_key = _wrapped_table_description_key(previous_fields, current_fields)
    if not description_key:
        return False  # 两行没有共同描述列时不能合并。
    if not _looks_like_wrapped_description_tail(current_fields[description_key]):
        return False  # 当前描述不像上一行的括号/百分比分布说明续行时不合并。
    if any(current_fields.get(key) for key in _table_value_or_context_keys()):
        return False  # 当前行自己有数值/单位/条件时更可能是真实独立行。
    previous_symbol = previous_fields.get("symbol", "")  # 符号列是合并上下标的主要证据。
    current_symbol = current_fields.get("symbol", "")  # 当前行的短符号通常是下标/后缀。
    if not previous_symbol or not current_symbol:
        return False  # 没有符号续写时，合并风险太高。
    if not _looks_like_symbol_subscript(current_symbol):
        return False  # 长文本符号不应拼接到上一行。
    return any(previous_fields.get(key) for key in _table_value_or_context_keys())


def _wrapped_table_description_key(previous_fields: dict[str, str], current_fields: dict[str, str]) -> str:
    """Return the description-like field shared by two candidate continuation rows."""

    for key in ("characteristic", "parameter", "description", "label", "name"):
        if previous_fields.get(key) and current_fields.get(key):
            return key  # 两行共享同一类身份列，才可能是同一表格行被换行拆开。
    return ""


def _table_value_or_context_keys() -> tuple[str, ...]:
    """Return table fields that indicate a row is complete on its own."""

    return ("condition", "value", "values", "min", "minimum", "typ", "typical", "max", "maximum", "unit", "units")


def _looks_like_wrapped_description_tail(value: str) -> bool:
    """Return True for a second physical line that continues a long description."""

    candidate = normalize_line(value)  # 描述续行通常以百分比、括号尾或小写连接词开头。
    if not candidate:
        return False
    if re.match(r"^\d+(?:\.\d+)?%", candidate):
        return True  # `99.9975% of ...` 是上一行括号说明的续写。
    if candidate.startswith(")") or candidate.endswith(")"):
        return True  # 括号闭合行通常属于上一行的长描述。
    return bool(re.match(r"(?i)^(?:of|the|and|or|from|to)\b", candidate))


def _merge_table_row_continuation(previous_line: str, current_line: str) -> str:
    """Combine a wrapped continuation row into the previous structured row."""

    table_token, previous_cells = _split_table_line_cells(previous_line)  # 拿到 T 编号和上一行单元格。
    _current_token, current_cells = _split_table_line_cells(current_line)  # 当前行只提供续写字段。
    current_fields = _table_line_field_map(current_line)  # 当前字段值用于拼接描述和符号。
    index_by_key = _table_cell_index_by_key(previous_cells)  # 保留上一行字段顺序，只更新必要单元格。
    description_key = _wrapped_table_description_key(_table_line_field_map(previous_line), current_fields)
    if description_key and description_key in index_by_key:
        index, label = index_by_key[description_key]
        previous_value = previous_cells[index].split("=", 1)[1]
        previous_cells[index] = f"{label}={normalize_line(previous_value + ' ' + current_fields[description_key])}"
    if "symbol" in index_by_key and current_fields.get("symbol"):
        index, label = index_by_key["symbol"]
        previous_value = previous_cells[index].split("=", 1)[1]
        previous_cells[index] = f"{label}={previous_value}{current_fields['symbol']}"
    return _format_existing_table_cells(table_token, previous_cells)


def _split_table_line_cells(line: str) -> tuple[str, list[str]]:
    """Return the internal table token and display cells from one formatted row."""

    text = normalize_line(line)  # 结构化行内部字段用竖线分隔。
    if text.startswith(_TABLE_ROW_PREFIX):
        text = text[len(_TABLE_ROW_PREFIX) :].strip()  # 去掉内部前缀，只解析 T 编号和字段。
    cells = [cell.strip() for cell in text.split("|") if cell.strip()]  # 空字段没有合并价值。
    if cells and re.fullmatch(r"T\d+", cells[0], flags=re.I):
        return cells[0], cells[1:]  # 标准结构化表格行带 T 编号。
    return "", cells


def _table_line_field_map(line: str) -> dict[str, str]:
    """Parse one formatted table row into normalized field names."""

    fields: dict[str, str] = {}  # key -> value，用于续行判断。
    _table_token, cells = _split_table_line_cells(line)
    for cell in cells:
        if "=" not in cell:
            continue
        key, value = cell.split("=", 1)
        normalized_key = normalize_line(key).casefold()
        normalized_value = normalize_line(value)
        if normalized_key and normalized_value:
            fields[normalized_key] = normalized_value
    return fields


def _table_cell_index_by_key(cells: list[str]) -> dict[str, tuple[int, str]]:
    """Return each ``Header=Value`` cell index while preserving display labels."""

    index_by_key: dict[str, tuple[int, str]] = {}  # key -> (位置, 原始表头标签)。
    for index, cell in enumerate(cells):
        if "=" not in cell:
            continue
        key, _value = cell.split("=", 1)
        normalized_key = normalize_line(key).casefold()
        if normalized_key:
            index_by_key[normalized_key] = (index, key)
    return index_by_key


def _format_existing_table_cells(table_token: str, cells: list[str]) -> str:
    """Format already-normalized table cells back into the internal row string."""

    prefix = f"{_TABLE_ROW_PREFIX} {table_token}" if table_token else _TABLE_ROW_PREFIX
    return f"{prefix} | " + " | ".join(cells)


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
        value = "".join(lines)
    else:
        value = " / ".join(lines)  # 普通多行说明用斜杠分隔，保留每个子项的可读边界。
    return re.sub(
        r"(?i)([×x*])\s*[×x*]\s*(?=10(?:\D|$))",
        r"\1",
        value,
    )  # PDF 字形映射偶尔把一个乘号抽成 ×x；指数记法里只保留一个乘号。


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

    return bool(re.fullmatch(r"[A-Za-z0-9_.+\-*/]+", value)) and len(value) <= 6 and " " not in value


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
    if len(words) >= 3 and re.search(
        r"(?i)\b(?:model|parameters?|characteristics?|requirements?|interface)\b",
        normalized,
    ):
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
            f"{label}={cell}"
            for label, cell in zip(labels, cells, strict=False)
            if cell
        ]  # 用 Header=Value 形式保留列含义，特别适合表格差异审阅。
    else:
        parts = [cell for cell in cells if cell]  # 没有表头时保留原始列顺序。
    if not parts:  # 所有单元格都为空时跳过。
        return ""
    return f"{_TABLE_ROW_PREFIX} T{table_number} | " + " | ".join(parts)


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


def _table_duplicate_token_index(
    table_lines: list[str],
) -> tuple[set[str], tuple[frozenset[str], ...]]:
    """Build page-wide and per-row token indexes from structured table rows."""

    tokens: set[str] = set()  # 聚合所有结构化表格 token，用于识别重复的原始表格长行。
    row_token_sets: list[frozenset[str]] = []  # 单行 token 用于确认短句确实完整复制了某一表格行。
    for table_line in table_lines:
        row_tokens = frozenset(_structured_table_value_tokens(table_line))
        if row_tokens:
            row_token_sets.append(row_tokens)
            tokens.update(row_tokens)  # 表头字段名不参与，避免全局 token 被 Parameter/Value 等常见词抬高。
    return tokens, tuple(row_token_sets)


def _looks_like_duplicate_unstructured_table_line(
    line: str,
    table_index: tuple[set[str], tuple[frozenset[str], ...]],
) -> bool:
    """Return True when raw extracted text duplicates structured table rows."""

    table_tokens, row_token_sets = table_index
    if not table_tokens:  # 没有结构化表格时，不能删除正文长行。
        return False
    words = set(re.findall(r"[A-Za-z]+", line.casefold()))  # 只用英文技术词判断，避免误伤中文段落。
    line_tokens = _unstructured_table_tokens(line)  # 计算该长行与结构化表格行的 token 重叠。
    if not line_tokens:
        return False
    if any(len(row_tokens) >= 4 and row_tokens <= line_tokens for row_tokens in row_token_sets):
        return True  # 原始行完整覆盖一条结构化记录时即为重复，修订历史短句即使以句点结尾也适用。
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


def _structured_table_value_tokens(table_line: str) -> set[str]:
    """Tokenize only visible cell values from one ``Header=Value`` table row."""

    text = normalize_line(table_line)
    if text.startswith(_TABLE_ROW_PREFIX):
        text = text[len(_TABLE_ROW_PREFIX) :].strip()
    values: list[str] = []
    for cell in (part.strip() for part in text.split("|") if part.strip()):
        if re.fullmatch(r"T\d+", cell, flags=re.I):
            continue
        if "=" in cell:
            _key, value = cell.split("=", 1)
            if value.strip():
                values.append(value.strip())
        else:
            values.append(cell)
    return _unstructured_table_tokens(" ".join(values))


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
