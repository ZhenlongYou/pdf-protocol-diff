"""PDF text extraction helpers.

The extractor requires ``pdfplumber`` so table rows, page-coordinate cleanup,
and table screenshots use one consistent layout source. Scan-like raster pages
can use a conservative Tesseract fallback when the external engine is present;
OCR-derived text always remains degraded evidence rather than proof of equality.
"""

from __future__ import annotations

import base64  # 把表格截图编码成 data URI，HTML 报告可以离线打开。
import hashlib  # 对实际交给解析器的 PDF 快照计算摘要，避免报告期重读路径造成 provenance 漂移。
import io  # 在内存中保存 JPEG 截图，避免生成临时图片文件。
import math  # 用页面对角线和字符间距确认完整水印簇，避免误删孤立旋转字母。
import re  # 使用正则识别行号边栏、表头和表格值模式。
import shutil  # 检测 tesseract 可执行文件是否存在，决定是否启用 OCR。
import tempfile  # 大 PDF 快照超过内存阈值时自动落到临时文件，仍保持解析字节与摘要一致。
from collections import (
    Counter,  # 比较表格 bbox、原始单元格与结构化行的字符覆盖，只有全覆盖才替换比较面。
)
from pathlib import Path
from statistics import median

from .formula_visuals import extract_formula_visuals
from .layout_blocks import (
    build_pdfplumber_text_blocks,
    extract_pdfplumber_coordinate_words,
    make_ocr_document_block,
    make_table_document_blocks,
    page_bounds,
    reassign_block_reading_order,
)
from .models import (
    DocumentBlock,
    ExtractionResult,
    FormulaVisual,
    PageText,
    TableVisual,
)
from .page_furniture import (
    looks_like_page_bearing_running_header,
)
from .page_ocr import (
    _extract_scan_page_text_with_evidence,
    classify_page_parser_route,
    normalize_ocr_language,
)
from .table_codec import (
    decode_table_cell,
    encode_table_field,
    escape_table_component,
    split_table_cells,
    split_table_field,
)
from .text_utils import (
    compact_inline,
    is_known_engineering_symbol_letter_suffix,
    normalize_line,  # 复用统一空白规整逻辑，保证提取层和比较层口径一致。
)


class MissingDependencyError(RuntimeError):
    """Raised when the user has not installed project requirements."""


class PdfReadError(RuntimeError):
    """Raised for PDF files that cannot be opened or decoded."""


_WATERMARK_MIN_FONT_SIZE = 40.0  # 大号 DRAFT 水印通常远大于正文；结合字符内容过滤，避免误删大标题。
_WATERMARK_TEXT_CHARS = frozenset("draftDRAFT")  # 只把明确来自 DRAFT 水印且旋转的大字母剔除，保留章节封面标题。
_LINE_NUMBER_MIN_COUNT = 24  # 破坏性裁边须超过常见 1..20 正文列表；真实 OIF 行号通常覆盖几十行。
_LINE_NUMBER_MIN_RUN = 20  # 同列还须有长连续段，零散页边数字不能借总数量证明成行号栏。
_LINE_NUMBER_COLUMN_TOLERANCE_RATIO = 0.006  # 行号按内侧边缘聚类；约 0.6% 页宽容纳字距误差但隔离正文数字。
_DOCUMENT_LINE_NUMBER_MIN_PAGES = 3  # 单页或两页长列表仍有语义歧义；至少三页才能证明出版级重复网格。
_DOCUMENT_LINE_NUMBER_MIN_PAGE_COVERAGE = 0.80  # 打印行号应覆盖绝大多数选定页，局部编号表不得获得全文删除权。
_DOCUMENT_LINE_NUMBER_LAST_VALUE = 49  # 当前只识别每页重置的 1..49 印刷行网格，避免泛化到任意正文列表。
_DOCUMENT_LINE_NUMBER_MIN_DISTINCT_VALUES = 45  # 允许 DRAFT 水印合并少量行号字形，但每页必须仍接近完整。
_DOCUMENT_LINE_NUMBER_MIN_SINGLETON_ANCHORS = 24  # 重复技术数字不能主导网格拟合；至少半页数值需各有唯一候选。
_DOCUMENT_LINE_NUMBER_MIN_ORPHAN_BASELINES = 8  # 真行号会给空白视觉行编号；真列表的每个序号通常都有同基线正文。
_DOCUMENT_LINE_NUMBER_GRID_ORIGIN_TOLERANCE = 0.006  # 跨页首行的归一化 y 位置需稳定，排除页内局部数字表。
_DOCUMENT_LINE_NUMBER_GRID_PITCH_TOLERANCE = 0.0015  # 跨页行距允许小量 PDF 坐标抖动，但不接受不同列表节奏。
_TABLE_ROW_PREFIX = "表格行:"  # 报告里的表格行标记，方便比较器把表格行当作独立审阅单元。
_TABLE_CAPTION_RE = re.compile(r"(?i)\btable\s+\d+(?:[-–]\d+)?\b|表\s*\d+")  # 识别真正表题，避免把图题当表格。
_FIGURE_CAPTION_RE = re.compile(r"(?i)\b(?:figure|fig\.)\s+\d+(?:[-–.]\d+)?\b|图\s*\d+")  # 识别图题/图片块，按用户要求不做图片对比。
_TABLE_HEADER_SCAN_ROWS = 12  # pdfplumber 有时把标题/注释放在表格开头，需要在前十余行内寻找表头。
_TABLE_SCREENSHOT_RESOLUTION = 144  # 表格截图使用 2x PDF 点阵，兼顾清晰度和 HTML 体积。
_TABLE_SCREENSHOT_PADDING = 10.0  # 截图在表格 bbox 外保留少量边距，方便看见表题和边框。
_UNSTRUCTURED_TABLE_MIN_CHARS = 160  # 超过该长度且数字密集的正文行，才可能是表格被抽成的一整行。
_COLUMN_STRONG_TEXT_WEIGHT = 12  # 少量双栏行只有在两侧都有较强正文证据时才触发布局风险。
_COLUMN_REORDER_MIN_SUPPORTING_ROWS = 3  # 改写正文顺序比“提示风险”更保守，至少要求三组平行正文行。
_COLUMN_SHORT_TEXT_WEIGHT = 8  # Limit L01 这类短标签需要更多行和更大跨度共同证明双栏。
_COLUMN_SHORT_MIN_SUPPORTING_LINES = 8  # 避免普通短表格的少数几行被误判成双栏正文。
_COLUMN_SHORT_MIN_PAGE_SPAN_RATIO = 0.25  # 短标签必须覆盖显著页面高度，不能只聚集在一个局部表格。
_LAYOUT_CHECK_MIN_TEXT_CHARACTERS = 100  # 中等正文页缺坐标证据就应告警，避免多页累计过质量门。
_LAYOUT_CHECK_MIN_COORDINATE_COVERAGE_RATIO = 0.20  # 坐标文字至少覆盖正文约五分之一，页眉页脚两行不能代表正文布局。
_GRID_MIN_TEXT_LINE_COVERAGE_RATIO = 0.60  # 局部小表不能掩盖覆盖整页的双栏正文证据。
_GRID_RULE_CLUSTER_MIN_GAP = 36.0  # 兼容常见 30pt 表格行距；远离的多个小表仍不能合并成整页网格。
_GRID_RULE_CLUSTER_LINE_GAP_MULTIPLIER = 1.75  # 允许表格行距略大于正文典型行距，同时切断远距离线框簇。
_COLUMN_GUTTER_FRACTIONS = tuple(index / 100 for index in range(28, 73, 2))  # 覆盖 30/70 到 70/30 常见主栏+侧栏；再靠边更像页边栏。
_TABLE_HEADER_ENGLISH_DESCRIPTOR_TERMS = frozenset({"parameter", "description", "characteristic", "symbol", "condition"})  # 英文无框表的字段/描述栏必须与值栏形成互补角色，正文里单独出现 requirement/condition 不足以判表。
_TABLE_HEADER_ENGLISH_VALUE_TERMS = frozenset({"requirement", "value", "minimum", "maximum", "unit", "units", "typical", "nominal", "min", "max"})  # 英文值栏表头词只在短首行标签形态中使用，不能扫描长正文句。
_TABLE_HEADER_ENGLISH_MAX_WORDS = 4  # 英文短表头通常是 1--4 个词；更长的句子即使含关键词也按正文处理。
_TABLE_HEADER_ENGLISH_MAX_CHARACTERS = 32  # 字符长度再约束短标签，避免紧凑词数的长技术句被错误拒绝。
_TABLE_HEADER_CJK_DESCRIPTOR_PREFIXES = frozenset({"参数", "描述", "特性", "符号", "条件"})  # CJK 无框表左栏常以字段/描述标签开头；只认行首，避免正文里的“测试条件”误触发。
_TABLE_HEADER_CJK_VALUE_PREFIXES = frozenset({"要求", "数值", "典型值", "标称值", "最小", "最大", "单位", "上限", "下限"})  # CJK 无框表对应值栏常以要求、值或单位标签开头，必须与字段栏配对才成立。
_TABLE_HEADER_CJK_MAX_CHARACTERS = 16  # 表头是紧凑标签；超过此长度更可能是完整正文句，宁可不据此拒绝列优先。
_ENGINEERING_UNIT_ABBREVIATION_PATTERN = r"(?:[fpnumkMGTµμ]?V|[fpnumkMGTµμ]?A|[fpnumkMGTµμ]?W|[fpnumkMGTµμ]?F|[fpnumkMGTµμ]?H|[fpnumµμ]?s|[fpnumkMGTµμ]?Hz|[kMGT]?bps|[kMGT]?b/s|[kMGT]?bit/s|[kMGT]?B/s|[kMGT]T/s|UI|dB(?:m|c)?|[kMGT]?Ω|ppm|°C)"  # 缩写只列出常见工程量；不能把任意英文单词误当单位。
_ENGINEERING_UNIT_WORD_PATTERN = r"(?i:volts?|millivolts?|microvolts?|nanovolts?|amps?|amperes?|milliamps?|milliamperes?|microamps?|microamperes?|watts?|milliwatts?|microwatts?|ohms?|kiloohms?|megaohms?|femtoseconds?|picoseconds?|nanoseconds?|microseconds?|milliseconds?|seconds?|hertz|kilohertz|megahertz|gigahertz|terahertz|(?:kilo|mega|giga|tera)?transfers?/s|(?:kilo|mega|giga|tera)?bits?/s|unit intervals?|decibels?)"  # 全拼单位同样使用有限词表，保留既有 Volts、Picoseconds 等 PDF 文本形态。
_NUMERIC_UNIT_VALUE_PATTERN = rf"[+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*(?:%|(?:{_ENGINEERING_UNIT_ABBREVIATION_PATTERN}|{_ENGINEERING_UNIT_WORD_PATTERN})(?=$|[^A-Za-z0-9_])|伏特|毫伏|微伏|安培|毫安|微安|皮秒|纳秒|微秒|毫秒|秒|千兆赫|兆赫|千赫|赫兹|兆欧|千欧|欧姆|毫瓦|瓦特)"  # 只把数字紧邻已知工程单位当值栅格；普通 “1 item” 一类正文计数不触发不重排边界。
_NUMERIC_UNIT_VALUE_ANYWHERE_RE = re.compile(_NUMERIC_UNIT_VALUE_PATTERN)  # 轻量路径无法可靠区分长标签表与数值双栏正文，跨行值栅格一律保留原始 y-first 顺序。
_NUMERIC_VALUE_GRID_MIN_ROWS = 2  # 至少两条同基线数字单位行才视作数值栅格；单个测量值不单独阻止正文重排。
_CONTINUOUS_NO_SPACE_TEXT_MIN_LETTERS = 8  # 无空格文字需达到足够字符量，才把单个 Unicode 字符串当作连续正文而非短表格单元格。
_MULTILEVEL_SECTION_ANCHOR_RE = re.compile(r"^(\d+(?:\.\d+){2,})\s+(.+)$")  # 只允许 31.3.10 这类多级数字标题证明跨标题错序。
_SECTION_ANCHOR_MIN_MOVED_CHARACTERS = 120  # 少量词序/空格差异不足以授权用坐标重建整页正文。
_SECTION_ANCHOR_NORMATIVE_PROSE_RE = re.compile(
    r"(?i)\b(?:shall|must|should|required|requirements?|specified|不得|必须|应当|要求|规定)\b"
)  # 锚点重排的否决门禁独立于通用图块分类器，保护句首规范条款。
_SIGNED_RATIONAL_SUBSCRIPT_RE = re.compile(
    r"[+\-−]?\d+(?:/\d+)?"
)  # 仅供坐标已证明的视觉下标；不在普通文字层做形状替换。


def extract_pdf_text(
    pdf_path: str | Path,
    start_page: int | None = None,
    end_page: int | None = None,
    ocr_language: str | None = None,
    layout_backend: str = "native",
) -> ExtractionResult:
    """Extract selectable text from a one-based inclusive PDF page range.

    Args:
        pdf_path: Path to the PDF file.
        start_page: First page to extract, using the page number shown by most
            PDF readers. ``None`` starts at page 1.
        end_page: Last page to extract, inclusive. ``None`` ends at the final
            page of this PDF.
        ocr_language: Optional Tesseract language expression for scan-like
            raster pages, for example ``chi_sim+eng``.
        layout_backend: ``native`` keeps the fast default path. ``auto`` and
            ``docling`` opt into conservative Docling repair for pages already
            marked as having a native reading-order risk.

    Returns:
        ExtractionResult with one PageText per page and non-fatal warnings.

    Raises:
        FileNotFoundError: The PDF path does not exist.
        MissingDependencyError: pdfplumber is missing.
        PdfReadError: pdfplumber cannot open the file.
        ValueError: The requested page range or OCR language is invalid.
    """

    path = Path(pdf_path).expanduser().resolve()  # 统一成绝对路径，报告和错误信息都能直接定位源文件。
    normalized_ocr_language = normalize_ocr_language(ocr_language)
    if not path.exists():  # 提前检查路径，避免底层 PDF 库给出难懂的打开错误。
        raise FileNotFoundError(f"PDF 文件不存在: {path}")

    try:  # 必须使用 pdfplumber，因为表格截图、坐标过滤和行级表格识别都依赖它。
        extraction = _extract_pdf_text_with_pdfplumber(
            path,
            start_page,
            end_page,
            normalized_ocr_language,
        )
        # 默认 native 在返回前不导入版面模块；普通 PDF 连正则/枚举初始化也不新增。
        if layout_backend is None or (
            isinstance(layout_backend, str)
            and layout_backend.strip().lower() == "native"
        ):
            return extraction
        # 只有显式选择可选路径时才导入；Docling 模型仍在模块内再次延迟导入。
        from .layout_backend import enrich_with_optional_layout_backend

        return enrich_with_optional_layout_backend(extraction, layout_backend)
    except ModuleNotFoundError as exc:  # 缺少 pdfplumber 时直接失败，禁止静默退回 pypdf。
        raise MissingDependencyError(
            "缺少依赖 pdfplumber，无法执行表格截图和坐标过滤。"
            "请在项目目录运行: .venv/bin/python -m pip install -r requirements.txt"
        ) from exc


def _extract_pdf_text_with_pdfplumber(
    path: Path,
    start_page: int | None,
    end_page: int | None,
    ocr_language: str | None = None,
) -> ExtractionResult:
    """Extract layout-aware text and structured table rows with pdfplumber."""

    try:  # 延迟导入使缺失依赖可以被清晰地转成用户可理解的错误。
        import pdfplumber
    except ModuleNotFoundError:  # 外层会转成明确的依赖安装提示，禁止退回低保真提取器。
        raise

    warnings: list[str] = []  # 收集非致命抽取问题，最终写进报告。
    source_snapshot = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024, mode="w+b")
    source_digest = hashlib.sha256()
    try:
        with path.open("rb") as source_handle:
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                source_digest.update(chunk)
                source_snapshot.write(chunk)
        source_snapshot.seek(0)
        pdf = pdfplumber.open(source_snapshot)
    except Exception as exc:  # 快照或 PDF 解析失败时给出包含文件名的错误。
        source_snapshot.close()
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
        formula_visuals: list[FormulaVisual] = []  # 显示公式单独保留无标注源截图和几何上下标。
        selected_pages = [
            (index, pdf.pages[index - 1])
            for index in range(selected_start, selected_end + 1)
        ]  # 先固定页窗并保留用户可见的 1-based 页码，供坐标证据复用。
        coordinate_pages = {
            index: _filtered_layout_page(
                page,
                coordinate_words=None,
                gutter_boxes=(),
                footer_boxes=(),
                header_boxes=(),
            )
            for index, page in selected_pages
        }  # 坐标词唯一观测先剔除完整 DRAFT 字形，避免水印与正文合成一个巨型词；页边事实仍原样保留。
        coordinate_evidence = {
            index: extract_pdfplumber_coordinate_words(coordinate_pages[index], index)
            for index, _page in selected_pages
        }  # 每页坐标词仍只读取一次，随后同时供跨页行号证明、块生成和阅读顺序检查。
        gutter_boxes_by_page = _document_proven_line_number_gutter_boxes(
            selected_pages,
            {index: evidence[0] for index, evidence in coordinate_evidence.items()},
            coordinate_issues_by_page={
                index: (evidence[1], evidence[2])
                for index, evidence in coordinate_evidence.items()
            },
        )  # 只有跨页完整重置网格与空白编号基线共同成立时，才从比较面剔除窄边列。
        ambiguous_gutter_sides_by_page = {
            index: (
                ()
                if gutter_boxes_by_page.get(index)
                else _candidate_line_number_gutter_sides(
                    page,
                    words=coordinate_evidence[index][0],
                )
            )
            for index, page in selected_pages
        }  # 已证明页的比较文本已干净，不得再让章节器盲剔合法的 `1 Introduction`。
        for index, page in selected_pages:
            visual_noise_bboxes = tuple(
                [
                    *gutter_boxes_by_page.get(index, ()),
                    *_proven_running_footer_boxes(
                        page,
                        words=coordinate_evidence[index][0],
                    ),
                    *_proven_running_header_boxes(
                        page,
                        words=coordinate_evidence[index][0],
                    ),
                ]
            )  # 视觉层只屏蔽抽取层本次已用坐标证明并删除的页眉、页脚和窄边区域；禁止固定比例裁边。
            (
                text,
                page_warnings,
                page_visuals,
                layout_risk,
                ocr_used,
                image_dominant,
                blocks,
            ) = _extract_pdfplumber_page_text(
                page,
                path.name,
                index,
                ocr_language=ocr_language,
                coordinate_evidence=coordinate_evidence[index],
                gutter_boxes=gutter_boxes_by_page.get(index, ()),
            )
            warnings.extend(page_warnings)  # 单页表格或文本抽取失败不应中断整份报告。
            table_visuals.extend(page_visuals)  # 表格截图单独积累，不混入普通正文。
            page_formulas, formula_warnings = extract_formula_visuals(
                page,
                coordinate_evidence[index][0],
                index,
                excluded_bboxes=tuple(visual.bbox for visual in page_visuals),
            )
            formula_visuals.extend(page_formulas)
            warnings.extend(formula_warnings)
            pages.append(
                PageText(
                    page_number=index,
                    text=text,
                    layout_risk=layout_risk,
                    ocr_used=ocr_used,
                    blocks=blocks,
                    image_dominant=image_dominant,
                    parser_route=classify_page_parser_route(
                        text=text,
                        layout_risk=layout_risk,
                        image_dominant=image_dominant,
                        ocr_used=ocr_used,
                    ),
                    page_bbox=page_bounds(page),
                    ambiguous_line_number_sides=ambiguous_gutter_sides_by_page[index],
                    visual_noise_bboxes=visual_noise_bboxes,
                )
            )  # 保留页码、图像/OCR 独立事实和互斥路由，供质量层与报告审计判断。
    finally:
        pdf.close()  # 明确关闭 pdfplumber 打开的文件资源。
        source_snapshot.close()  # 解析器始终读取这份快照；摘要与实际解析字节严格绑定。

    return _finalize_extraction_result(
        path=path,
        pages=pages,
        warnings=warnings,
        total_pages=total_pages,
        selected_start=selected_start,
        selected_end=selected_end,
        table_visuals=table_visuals,
        formula_visuals=formula_visuals,
        source_sha256=source_digest.hexdigest(),
    )  # 统一追加空页和低字符数警告。


def _extract_pdfplumber_page_text(
    page: object,
    pdf_name: str,
    page_number: int,
    *,
    ocr_language: str | None = None,
    coordinate_evidence: tuple[
        list[dict[str, float | str]],
        list[str],
        str | None,
    ] | None = None,
    gutter_boxes: tuple[tuple[float, float, float, float], ...] | None = None,
) -> tuple[
    str,
    list[str],
    list[TableVisual],
    bool,
    bool,
    bool,
    tuple[DocumentBlock, ...],
]:
    """Extract one page after removing layout noise and adding table rows."""

    warnings: list[str] = []  # 单页内部的非致命问题独立收集，便于定位页码。
    if coordinate_evidence is None:
        coordinate_words, block_warnings, coordinate_error = extract_pdfplumber_coordinate_words(
            page,
            page_number,
        )  # 独立单页调用仍只读取一次坐标，但缺少跨页证据时不能删除数字列。
    else:
        coordinate_words, block_warnings, coordinate_error = coordinate_evidence
    ambiguous_line_number_column = bool(
        _candidate_line_number_gutter_boxes(page, words=coordinate_words)
    )  # 只把行号状数字列作为风险证据，禁止据此删除可比较内容。
    gutter_boxes = gutter_boxes if gutter_boxes is not None else ()
    footer_boxes = _proven_running_footer_boxes(page, words=coordinate_words)
    # A top-margin title can be cover/revision content on one page.  Header
    # removal therefore waits for the document-wide repetition proof in
    # sectioning instead of deleting from single-page shape alone.
    header_boxes: tuple[tuple[float, float, float, float], ...] = ()
    filtered_page = _filtered_layout_page(
        page,
        coordinate_words=coordinate_words,
        gutter_boxes=gutter_boxes,
        footer_boxes=footer_boxes,
        header_boxes=header_boxes,
    )  # 已证明行号只从比较页剔除；无跨页证据时仍返回原页。
    native_blocks = build_pdfplumber_text_blocks(
        coordinate_words,
        page_number,
    )  # 在比较面过滤之前建立坐标块，原始行号/页脚事实仍可审计。
    comparison_coordinate_words = _filter_coordinate_words_for_layout_noise(
        coordinate_words,
        gutter_boxes=gutter_boxes,
        footer_boxes=footer_boxes,
        header_boxes=header_boxes,
    )
    warnings.extend(block_warnings)  # 坐标块失败不能中断正文，但必须进入最终审计告警。
    (
        layout_risk,
        layout_warning,
        layout_evidence_insufficient,
        coordinate_text_characters,
    ) = (
        _assess_page_reading_order(
            filtered_page,
            words=comparison_coordinate_words,
            coordinate_error=coordinate_error,
        )
    )
    if layout_warning:
        warnings.append(
            f"{pdf_name}: 第 {page_number} 页阅读顺序坐标检查失败: {layout_warning}"
        )
    if gutter_boxes:
        warnings.append(
            f"{pdf_name}: 第 {page_number} 页的窄边数字列已由至少三页的重置 1..49 "
            "稳定网格与空白编号基线共同证明为打印行号；"
            "已证明并从比较文本过滤，原始坐标块仍保留供审计。"
        )
    elif ambiguous_line_number_column:
        layout_risk = True
        warnings.append(
            f"{pdf_name}: 第 {page_number} 页存在无法与正文列表可靠区分的页边数字列；"
            "已保留全部数字并标记需人工复核。"
        )
    try:
        text = filtered_page.extract_text(x_tolerance=1, y_tolerance=3) or ""
    except Exception as exc:  # 正文抽取失败时仍尝试表格，尽量保留可用信息。
        text = ""
        warnings.append(f"{pdf_name}: 第 {page_number} 页布局文本抽取失败: {exc}")

    # pdfplumber 默认按同一 y 基线行优先输出；只有坐标词完整覆盖且高证据证明
    # 两个平行正文栏时，才用可复核的 column-major 顺序替换该默认顺序。
    column_major_text = _high_confidence_column_major_text(
        filtered_page,
        comparison_coordinate_words,
    )
    if column_major_text is not None:
        text = column_major_text
    else:
        text = _repair_unique_section_anchor_reading_order(
            text,
            comparison_coordinate_words,
        )  # 单栏页只有唯一章节锚点证明大块内容跨标题错序时，才采用坐标 y-first 顺序。
    text = _repair_body_visual_subscript_order_with_duplicate_fallback(
        text,
        filtered_page,
        comparison_coordinate_words,
        block_warnings,
        coordinate_error,
    )  # 只按已验证坐标把视觉下标放回相邻基符号；图中精确重叠词仅触发一次字符守恒的原始坐标复核。
    text = _clean_extracted_page_text(text)  # 这里只规整空白；内容删除必须来自前面的坐标级证据。
    text, ocr_warnings, ocr_used, image_dominant, raw_ocr_text = _extract_scan_page_text_with_evidence(
        filtered_page,
        text,
        pdf_name,
        page_number,
        ocr_language=ocr_language,
    )
    warnings.extend(ocr_warnings)
    # 图像主导、OCR 与阅读顺序风险是独立事实；质量层直接依据前两者降级，不能伪装成双栏风险。
    if _page_may_contain_table(filtered_page, text):  # 只有疑似表格页才调用较慢的表格识别。
        (
            table_lines,
            table_visuals,
            table_warnings,
            fully_covered_table_bboxes,
        ) = _extract_table_lines_and_visuals(
            filtered_page,
            pdf_name,
            page_number,
            geometry_words=comparison_coordinate_words,
        )
        warnings.extend(table_warnings)  # 表格失败不阻塞正文比较。
    else:
        table_lines = []  # 普通正文页无需追加结构化表格行。
        table_visuals = []  # 普通正文页也不生成表格截图。
        fully_covered_table_bboxes = ()
    if fully_covered_table_bboxes and not ocr_used:
        text_without_tables = _extract_text_without_proven_table_bboxes(
            filtered_page,
            comparison_coordinate_words,
            fully_covered_table_bboxes,
            coordinate_warnings=block_warnings,
            coordinate_error=coordinate_error,
        )
        if text_without_tables is not None:
            text = _clean_extracted_page_text(text_without_tables)
    ocr_blocks = []  # 仅实际成功的整页 OCR 才能形成独立来源块。
    if ocr_used:
        ocr_block, ocr_block_warning = make_ocr_document_block(
            filtered_page,
            page_number,
            raw_ocr_text or "",
        )  # OCR 块只保存引擎原始观测，不能把合并后的原生文字误归因给 tesseract。
        if ocr_block_warning:
            warnings.append(ocr_block_warning)  # 边界异常时保留 OCR 事实并提示缺失块证据。
        if ocr_block is not None:
            ocr_blocks.append(ocr_block)  # 追加的 OCR 证据不替换原生坐标块。
    table_blocks, table_block_warnings = make_table_document_blocks(
        table_visuals,
        page_number,
    )  # 每个已接受 TableVisual 复用自身 bbox 和行级摘要建立表格块。
    warnings.extend(table_block_warnings)  # 表格块几何异常不能影响既有表格比较文本。
    blocks = reassign_block_reading_order(
        (*native_blocks, *ocr_blocks, *table_blocks)
    )  # 所有来源共享一个连续页内序号，方便后续页面路由和审计。
    combined_text = _combine_text_and_table_lines(text, table_lines)
    comparable_text_characters = len(re.sub(r"\s+", "", combined_text))
    coordinate_coverage_insufficient = (
        coordinate_text_characters is not None
        and comparable_text_characters >= _LAYOUT_CHECK_MIN_TEXT_CHARACTERS
        and coordinate_text_characters / comparable_text_characters
        < _LAYOUT_CHECK_MIN_COORDINATE_COVERAGE_RATIO
    )
    if (
        comparable_text_characters >= _LAYOUT_CHECK_MIN_TEXT_CHARACTERS
        and (layout_evidence_insufficient or coordinate_coverage_insufficient)
    ):
        warnings.append(
            f"{pdf_name}: 第 {page_number} 页阅读顺序坐标检查证据不足: "
            "坐标文字不足以覆盖已抽取的大量可比较文字。"
        )
    # 同时返回图像事实，调用方据此计算互斥路由而不反向解析告警文本。
    return (
        combined_text,
        warnings,
        table_visuals,
        layout_risk,
        ocr_used,
        image_dominant,
        blocks,
    )


def _clean_extracted_page_text(text: str) -> str:
    """Normalize text without deleting content based on text shape alone."""

    cleaned_lines: list[str] = []
    for raw_line in text.splitlines():
        line = normalize_line(raw_line)
        if not line:
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _repair_unique_section_anchor_reading_order(
    text: str,
    words: list[dict[str, float | str]],
) -> str:
    """Use y-first words only when one section anchor proves a large block swap.

    Some PDF content streams emit a formula that is visually above a section
    heading after the heading's body.  A wholesale coordinate rebuild would be
    unsafe without a semantic boundary.  This path therefore requires exactly
    one multi-level numeric heading in both observations, complete character
    conservation, and at least 120 coordinate characters that form a strict
    superset before that same heading.  The block moved across the anchor must
    also have formula/plot shape; ordinary prose is never reordered merely
    because its character multiset happens to fit.
    """

    raw_lines = [
        normalized
        for line in text.splitlines()
        if (normalized := normalize_line(line))
    ]
    if not raw_lines or not words:
        return text
    coordinate_lines = [
        _words_to_visual_line(line)
        for line in _visual_word_lines(words)
    ]
    if not coordinate_lines:
        return text
    coordinate_text = "\n".join(coordinate_lines)
    raw_characters = _non_whitespace_character_counts("\n".join(raw_lines))
    coordinate_characters = _non_whitespace_character_counts(coordinate_text)
    if not raw_characters or raw_characters != coordinate_characters:
        return text

    raw_anchors = _multilevel_section_anchor_occurrences(raw_lines)
    coordinate_anchors = _multilevel_section_anchor_occurrences(coordinate_lines)
    if len(raw_anchors) != 1 or len(coordinate_anchors) != 1:
        return text
    raw_anchor_index, raw_anchor = raw_anchors[0]
    coordinate_anchor_index, coordinate_anchor = coordinate_anchors[0]
    if _character_signature(raw_anchor) != _character_signature(coordinate_anchor):
        return text

    raw_prefix = _character_signature("\n".join(raw_lines[:raw_anchor_index]))
    coordinate_prefix = _character_signature(
        "\n".join(coordinate_lines[:coordinate_anchor_index])
    )
    raw_suffix_text = "\n".join(raw_lines[raw_anchor_index + 1 :])
    raw_suffix = _character_signature(raw_suffix_text)
    coordinate_suffix = _character_signature(
        "\n".join(coordinate_lines[coordinate_anchor_index + 1 :])
    )
    if not raw_suffix.startswith(coordinate_suffix):
        return text  # 锚点及其后正文必须逐字符同序；只允许原末尾块整体移到锚点之前。
    moved_block = _text_remainder_after_character_prefix(
        raw_suffix_text,
        coordinate_suffix,
    )
    if moved_block is None:
        return text  # 被移动块必须从完整原始行边界开始，不能把一条正文从中间切开。
    moved_suffix = _character_signature(moved_block)
    if len(moved_suffix) < _SECTION_ANCHOR_MIN_MOVED_CHARACTERS:
        return text
    if Counter(coordinate_prefix) != Counter(raw_prefix) + Counter(moved_suffix):
        return text  # 坐标前缀必须恰好由原前缀和被移动块组成，不接受增删字符。
    if not _is_character_subsequence(raw_prefix, coordinate_prefix):
        return text  # 原锚点前正文必须保持逐字符顺序；只容许公式块穿插到它之前。
    if _starts_with_normative_prose(moved_block):
        return text  # 含 insertion loss/frequency/连续数值的句首 shall 条款仍是正文，绝不跨标题移动。
    if not _looks_like_plot_or_formula_block(moved_block):
        return text  # 普通条款即使字符守恒也绝不因坐标顺序而跨章节锚点移动。
    return coordinate_text


def _multilevel_section_anchor_occurrences(
    lines: list[str],
) -> list[tuple[int, str]]:
    """Return substantial ``N.N.N Title`` lines as potential order anchors."""

    anchors: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _MULTILEVEL_SECTION_ANCHOR_RE.fullmatch(normalize_line(line))
        if match is None:
            continue
        title = match.group(2)
        if len(line) > 180:
            continue
        title_letters = re.findall(r"[^\W\d_]", title, flags=re.UNICODE)
        if len(title_letters) < 8:
            continue
        anchors.append((index, normalize_line(line)))
    return anchors


def _repair_body_visual_subscript_order(
    text: str,
    words: list[dict[str, float | str]],
) -> str:
    """Rejoin coordinate-proven body subscripts without changing other lines.

    pdfplumber can put smaller lowered text on the next extracted line even
    though it visually touches a base symbol.  The existing
    :func:`_words_form_visual_subscript` geometry is sufficient to recognize
    that relation; this repair only binds one-to-one relations whose physical
    source lines each have one unique occurrence in the extracted text.

    Replacements are made at those occurrence-bound lines, not by increasing
    the page-wide y tolerance.  Complete coordinate coverage and a final
    non-whitespace character multiset check make the rewrite lossless.  Any
    missing word, duplicate occurrence, ambiguous edge, or non-adjacent raw
    line therefore leaves the original text untouched.
    """

    raw_lines = [
        normalized
        for line in text.splitlines()
        if (normalized := normalize_line(line))
    ]
    if not raw_lines or len(words) < 2:
        return text

    observed_words: list[dict[str, object]] = []
    for word in words:
        try:
            observed_text = normalize_line(str(word.get("text", "")))
            x0 = float(word["x0"])
            x1 = float(word["x1"])
            top = float(word["top"])
            bottom = float(word["bottom"])
        except (KeyError, TypeError, ValueError):
            return text
        if not observed_text or x1 <= x0 or bottom <= top:
            return text
        observed_words.append(
            {
                "text": observed_text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": bottom,
            }
        )

    raw_characters = _non_whitespace_character_counts("\n".join(raw_lines))
    coordinate_characters = _non_whitespace_character_counts(
        "".join(str(word["text"]) for word in observed_words)
    )
    if not raw_characters or raw_characters != coordinate_characters:
        return text  # 坐标词未完整覆盖比较文本时，不能用局部几何改写整页正文。

    candidate_edges = [
        (base_index, suffix_index)
        for base_index, base_word in enumerate(observed_words)
        for suffix_index, suffix_word in enumerate(observed_words)
        if base_index != suffix_index
        and _body_words_form_visual_subscript(base_word, suffix_word)
    ]
    if not candidate_edges:
        return text
    endpoint_degrees = Counter(
        index
        for edge in candidate_edges
        for index in edge
    )
    one_to_one_edges = [
        edge
        for edge in candidate_edges
        if all(endpoint_degrees[index] == 1 for index in edge)
    ]
    if not one_to_one_edges:
        return text

    indexed_lines = _indexed_visual_word_lines(observed_words)
    line_for_word = {
        word_index: line_index
        for line_index, line in enumerate(indexed_lines)
        for word_index in line
    }
    coordinate_line_texts = [
        _words_to_visual_line([observed_words[index] for index in line])
        for line in indexed_lines
    ]
    coordinate_occurrences: dict[str, list[int]] = {}
    raw_occurrences: dict[str, list[int]] = {}
    for line_index, line in enumerate(coordinate_line_texts):
        coordinate_occurrences.setdefault(_character_signature(line), []).append(
            line_index
        )
    for line_index, line in enumerate(raw_lines):
        raw_occurrences.setdefault(_character_signature(line), []).append(line_index)

    coordinate_to_raw: dict[int, int] = {}
    for signature, coordinate_indexes in coordinate_occurrences.items():
        raw_indexes = raw_occurrences.get(signature, [])
        if len(coordinate_indexes) == len(raw_indexes) == 1:
            coordinate_to_raw[coordinate_indexes[0]] = raw_indexes[0]

    # pdfplumber can emit lowered spans in the same raw text line (with an
    # artificial space) even though its coordinate words correctly put those
    # spans on the immediately lower visual line.  Bind that exact composite
    # row only when every word on the lower line is a one-to-one suffix and the
    # combined character sequence occurs once in the raw text.  This covers
    # ``J RMS`` / ``J4u 03`` without doing global text-shaped substitutions.
    inline_replacements: dict[int, str] = {}
    inline_edges: set[tuple[int, int]] = set()
    edges_by_line_pair: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for edge in one_to_one_edges:
        base_line = line_for_word.get(edge[0])
        suffix_line = line_for_word.get(edge[1])
        if (
            base_line is None
            or suffix_line is None
            or suffix_line != base_line + 1
        ):
            continue
        edges_by_line_pair.setdefault((base_line, suffix_line), []).append(edge)
    for (base_line, suffix_line), edges in edges_by_line_pair.items():
        suffix_indexes_for_pair = {suffix_index for _base_index, suffix_index in edges}
        if set(indexed_lines[suffix_line]) != suffix_indexes_for_pair:
            continue
        combined_indexes = sorted(
            [*indexed_lines[base_line], *indexed_lines[suffix_line]],
            key=lambda index: (
                float(observed_words[index]["x0"]),
                float(observed_words[index]["x1"]),
                float(observed_words[index]["top"]),
                str(observed_words[index]["text"]),
            ),
        )
        combined_text = _words_to_visual_line(
            [observed_words[index] for index in combined_indexes]
        )
        raw_indexes = raw_occurrences.get(_character_signature(combined_text), [])
        if len(raw_indexes) != 1 or raw_indexes[0] in inline_replacements:
            continue
        rebuilt = _rebuild_body_subscript_visual_line(
            combined_indexes,
            observed_words,
            edges,
        )
        if _character_signature(rebuilt) != _character_signature(combined_text):
            continue
        inline_replacements[raw_indexes[0]] = rebuilt
        inline_edges.update(edges)

    accepted_edges: list[tuple[int, int]] = []
    for base_index, suffix_index in one_to_one_edges:
        if (base_index, suffix_index) in inline_edges:
            continue
        base_line = line_for_word.get(base_index)
        suffix_line = line_for_word.get(suffix_index)
        if base_line is None or suffix_line is None:
            continue
        base_raw_line = coordinate_to_raw.get(base_line)
        suffix_raw_line = coordinate_to_raw.get(suffix_line)
        if (
            suffix_raw_line is None
            and base_raw_line is not None
            and suffix_line != base_line
        ):
            adjacent_raw_line = base_raw_line + 1
            suffix_signature = _character_signature(
                coordinate_line_texts[suffix_line]
            )
            if (
                adjacent_raw_line < len(raw_lines)
                and _character_signature(raw_lines[adjacent_raw_line])
                == suffix_signature
                and adjacent_raw_line not in coordinate_to_raw.values()
            ):
                coordinate_to_raw[suffix_line] = adjacent_raw_line
                suffix_raw_line = adjacent_raw_line
                # 重复 suffix 只凭“唯一基行的紧邻原始行”绑定；重复基行或映射冲突仍 fail-closed。
        if base_raw_line is None or suffix_raw_line is None:
            continue
        if suffix_raw_line not in {base_raw_line, base_raw_line + 1}:
            continue  # 跨越普通正文行的较小文字不是可安全重排的相邻视觉下标。
        accepted_edges.append((base_index, suffix_index))
    if not accepted_edges and not inline_replacements:
        return text

    suffix_indexes = {suffix_index for _base_index, suffix_index in accepted_edges}
    suffix_for_base = dict(accepted_edges)
    touched_coordinate_lines = {
        line_for_word[index]
        for edge in accepted_edges
        for index in edge
    }
    replacements: dict[int, str] = dict(inline_replacements)
    for coordinate_line_index in touched_coordinate_lines:
        rebuilt_words: list[dict[str, object]] = []
        for word_index in indexed_lines[coordinate_line_index]:
            if word_index in suffix_indexes:
                continue
            word = dict(observed_words[word_index])
            suffix_index = suffix_for_base.get(word_index)
            if suffix_index is not None:
                suffix = observed_words[suffix_index]
                word["text"] = f'{word["text"]}{suffix["text"]}'
                word["x0"] = min(float(word["x0"]), float(suffix["x0"]))
                word["x1"] = max(float(word["x1"]), float(suffix["x1"]))
                word["top"] = min(float(word["top"]), float(suffix["top"]))
                word["bottom"] = max(
                    float(word["bottom"]),
                    float(suffix["bottom"]),
                )
            rebuilt_words.append(word)
        raw_line_index = coordinate_to_raw[coordinate_line_index]
        if raw_line_index in replacements:
            continue
        replacements[raw_line_index] = (
            _words_to_visual_line(rebuilt_words) if rebuilt_words else ""
        )

    repaired_lines = [
        replacements.get(line_index, line)
        for line_index, line in enumerate(raw_lines)
    ]
    repaired = "\n".join(line for line in repaired_lines if line)
    if _non_whitespace_character_counts(repaired) != raw_characters:
        return text
    return repaired


def _repair_body_visual_subscript_order_with_duplicate_fallback(
    text: str,
    page: object,
    coordinate_words: list[dict[str, float | str]],
    coordinate_warnings: list[str],
    coordinate_error: str | None,
) -> str:
    """Retry subscript repair with lossless raw words after exact deduplication.

    Coordinate blocks intentionally collapse identical overlapping words so a
    duplicated figure label cannot count as independent layout evidence.  The
    page text extractor, however, conserves both painted copies.  When that
    *specific* benign warning is the only coordinate issue, the deduplicated
    character multiset no longer covers the page and can unnecessarily veto an
    unrelated unique body subscript.  A second public ``extract_words`` view is
    therefore used only for this local repair.  The underlying repair still
    requires complete page-wide character conservation, unique line binding,
    one-to-one geometry, and a lossless final multiset.
    """

    repaired = _repair_body_visual_subscript_order(text, coordinate_words)
    if repaired != text:
        return repaired
    if not coordinate_warnings or not _coordinate_issues_preserve_line_number_grid_evidence(
        coordinate_warnings,
        coordinate_error,
    ):
        return repaired
    try:
        raw_words = page.extract_words(
            keep_blank_chars=False,
            use_text_flow=False,
            extra_attrs=["size"],
        ) or []
    except Exception:
        return repaired
    if not isinstance(raw_words, list):
        return repaired
    return _repair_body_visual_subscript_order(text, raw_words)


def _rebuild_body_subscript_visual_line(
    word_indexes: list[int],
    words: list[dict[str, object]],
    edges: list[tuple[int, int]],
) -> str:
    """Join proven suffixes while preserving every other word on one visual row."""

    suffix_indexes = {suffix_index for _base_index, suffix_index in edges}
    suffix_for_base = dict(edges)
    rebuilt_words: list[dict[str, object]] = []
    for word_index in word_indexes:
        if word_index in suffix_indexes:
            continue
        word = dict(words[word_index])
        suffix_index = suffix_for_base.get(word_index)
        if suffix_index is not None:
            suffix = words[suffix_index]
            word["text"] = f'{word["text"]}{suffix["text"]}'
            word["x0"] = min(float(word["x0"]), float(suffix["x0"]))
            word["x1"] = max(float(word["x1"]), float(suffix["x1"]))
            word["top"] = min(float(word["top"]), float(suffix["top"]))
            word["bottom"] = max(float(word["bottom"]), float(suffix["bottom"]))
        rebuilt_words.append(word)
    return _words_to_visual_line(rebuilt_words) if rebuilt_words else ""


def _indexed_visual_word_lines(
    words: list[dict[str, object]],
) -> list[list[int]]:
    """Group word indexes with the extractor's existing 3-point line tolerance."""

    ordered_indexes = sorted(
        range(len(words)),
        key=lambda index: (
            float(words[index]["top"]),
            float(words[index]["x0"]),
            float(words[index]["x1"]),
            str(words[index]["text"]),
        ),
    )
    lines: list[list[int]] = []
    for index in ordered_indexes:
        if (
            not lines
            or abs(
                float(words[index]["top"])
                - float(words[lines[-1][0]]["top"])
            )
            > 3.0
        ):
            lines.append([index])
        else:
            lines[-1].append(index)
    return lines


def _character_signature(value: str) -> str:
    """Return the exact character sequence while ignoring layout whitespace."""

    return "".join(character for character in value if not character.isspace())


def _is_character_subsequence(needle: str, haystack: str) -> bool:
    """Return whether ``needle`` appears in ``haystack`` without reordering."""

    position = 0
    for character in haystack:
        if position < len(needle) and character == needle[position]:
            position += 1
    return position == len(needle)


def _text_remainder_after_character_prefix(
    text: str,
    prefix: str,
) -> str | None:
    """Return a line-boundary remainder after an exact whitespace-free prefix."""

    if not prefix:
        return text
    matched = 0
    split_index: int | None = None
    for index, character in enumerate(text):
        if character.isspace():
            continue
        if matched >= len(prefix) or character != prefix[matched]:
            return None
        matched += 1
        if matched == len(prefix):
            split_index = index + 1
            break
    if split_index is None:
        return None
    remainder = text[split_index:]
    if not remainder.strip():
        return ""
    leading_whitespace = remainder[: len(remainder) - len(remainder.lstrip())]
    if "\n" not in leading_whitespace:
        return None
    return remainder.strip()


def _starts_with_normative_prose(value: str) -> bool:
    """Reject a leading prose clause before applying broad plot heuristics."""

    candidate = normalize_line(value)
    if not candidate:
        return False
    match = _SECTION_ANCHOR_NORMATIVE_PROSE_RE.search(candidate[:180])
    if match is None:
        return False
    first_token = candidate.split(maxsplit=1)[0]
    after_first_token = candidate[len(first_token) :].lstrip()
    if (
        re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", first_token)
        and any(character.isdigit() for character in first_token)
        and re.match(r"[=≤≥<>+*/]", after_first_token)
    ):
        return False  # 只豁免严格 `SCC22 = ...` 公式；`1.`/`Clause31 The ...` 仍按正文保护。
    prose_tokens = re.findall(
        r"[A-Za-z]{2,}|[\u3400-\u4dbf\u4e00-\u9fff]+",
        candidate[: match.end()],
    )
    return len(prose_tokens) >= 2


def _high_confidence_column_major_text(
    page: object,
    words: list[dict[str, float | str]],
) -> str | None:
    """仅在完整坐标强证据下，把真正平行双栏重排为左栏后右栏。

    ``pdfplumber.extract_text`` 的默认 y-first 顺序适合表格，但会把两栏正文
    交织。此函数宁可返回 ``None``：只有页面所有坐标词都能落入同一候选左右栏、
    至少三条同 y 强正文行跨越宽 gutter、两栏在纵向真正重叠且没有局部网格证据时，
    才建立 column-major 文本。调用方仍保留 ``layout_risk``，不因此提升质量等级。
    """

    # 坐标词数量过少时既无法证明两栏，也不应覆盖 pdfplumber 的原始正文。
    word_lines = _visual_word_lines(words)
    if len(word_lines) < _COLUMN_REORDER_MIN_SUPPORTING_ROWS:
        return None
    x_start, x_end = _page_horizontal_bounds(page)
    page_width = x_end - x_start
    page_height = float(getattr(page, "height", 0) or 0)
    if page_width <= 0 or page_height <= 0:
        return None
    minimum_gap = max(30.0, page_width * 0.08)
    minimum_vertical_span = max(12.0, page_height * 0.02)

    for fraction in _COLUMN_GUTTER_FRACTIONS:
        gutter_x = x_start + page_width * fraction
        left_lines: list[tuple[float, str]] = []
        right_lines: list[tuple[float, str]] = []
        supporting_tops: list[float] = []
        left_support_tops: list[float] = []
        right_support_tops: list[float] = []
        complete_partition = True

        for word_line in word_lines:
            # 词跨越 gutter 说明该视觉行不是两个独立栏；禁止丢词或猜测拆分。
            if any(float(word["x0"]) < gutter_x < float(word["x1"]) for word in word_line):
                complete_partition = False
                break
            left_words = [word for word in word_line if float(word["x1"]) <= gutter_x]
            right_words = [word for word in word_line if float(word["x0"]) >= gutter_x]
            if len(left_words) + len(right_words) != len(word_line):
                complete_partition = False
                break
            top = min(float(word["top"]) for word in word_line)
            if left_words:
                left_lines.append((top, _words_to_visual_line(left_words)))
            if right_words:
                right_lines.append((top, _words_to_visual_line(right_words)))
            # 只有同一纵向基线两侧均为强正文，才算改写顺序所需的正证据。
            if (
                left_words
                and right_words
                and _word_text_weight(left_words) >= _COLUMN_STRONG_TEXT_WEIGHT
                and _word_text_weight(right_words) >= _COLUMN_STRONG_TEXT_WEIGHT
            ):
                left_edge = max(float(word["x1"]) for word in left_words)
                right_edge = min(float(word["x0"]) for word in right_words)
                if right_edge - left_edge >= minimum_gap:
                    supporting_tops.append(top)
                    left_support_tops.append(top)
                    right_support_tops.append(top)

        if not complete_partition:
            continue
        # 两侧必须各有不少于三条同基线强正文，且相同 y 范围确实重叠；这样不把
        # 上下分栏、局部旁注或无框数字表误认为阅读顺序可以安全改写。
        if len(supporting_tops) < _COLUMN_REORDER_MIN_SUPPORTING_ROWS:
            continue
        vertical_span = max(supporting_tops) - min(supporting_tops)
        overlap_start = max(min(left_support_tops), min(right_support_tops))
        overlap_end = min(max(left_support_tops), max(right_support_tops))
        if vertical_span < minimum_vertical_span or overlap_end < overlap_start:
            continue
        if _has_local_grid_evidence(
            page,
            supporting_tops,
            gutter_x=gutter_x,
            minimum_gap=minimum_gap,
        ):
            continue
        if not _has_parallel_prose_column_evidence(left_lines, right_lines):
            continue  # 无框表也没有规则线；只有连续正文证据才能获准改写成列优先顺序。
        # 坐标本身决定行内左右词序和栏内上下顺序，不借用原始抽取字符串猜测。
        left_text = "\n".join(text for _top, text in sorted(left_lines))
        right_text = "\n".join(text for _top, text in sorted(right_lines))
        if left_text and right_text:
            return f"{left_text}\n{right_text}"
    return None


def _has_parallel_prose_column_evidence(
    left_lines: list[tuple[float, str]],
    right_lines: list[tuple[float, str]],
) -> bool:
    """Return True only when same-baseline pairs look like continuous prose, not a borderless table."""

    # 同一 y 基线是现有双栏证据的基础；只分析两栏同时出现的行，避免旁注混入。
    left_by_top = {top: text for top, text in left_lines}
    right_by_top = {top: text for top, text in right_lines}
    common_tops = sorted(set(left_by_top) & set(right_by_top))
    if len(common_tops) < _COLUMN_REORDER_MIN_SUPPORTING_ROWS:
        return False
    first_top = common_tops[0]
    # 无框表的列角色只应由最先出现的同基线紧凑表头证明，不能扫描正文中的普通术语子串。
    if _looks_like_borderless_table_header_pair(
        left_by_top[first_top],
        right_by_top[first_top],
    ):
        return False
    # 轻量坐标规则无法可靠区分数值双栏正文与长标签参数表；重复值栅格一律不猜测 column-major。
    if _has_repeated_numeric_value_grid(
        left_by_top,
        right_by_top,
        common_tops,
    ):
        return False
    for top in common_tops:
        left_text = left_by_top[top]
        right_text = right_by_top[top]
        # 真正平行正文栏的每个候选行都需有连续 Unicode 文字证据；短标签/纯数值不够支撑顺序改写。
        if not _looks_like_continuous_prose_line(left_text) or not _looks_like_continuous_prose_line(right_text):
            return False
    return True


def _looks_like_borderless_table_header_pair(left_text: str, right_text: str) -> bool:
    """Recognize paired parameter/value header terms without requiring drawn grid lines."""

    # 英文表头必须是短的首行标签且左右角色互补，不能把技术正文中的常见词当成表头。
    left_terms = {term.casefold() for term in re.findall(r"[A-Za-z]+", left_text)}
    right_terms = {term.casefold() for term in re.findall(r"[A-Za-z]+", right_text)}
    if (
        len(left_terms) <= _TABLE_HEADER_ENGLISH_MAX_WORDS
        and len(right_terms) <= _TABLE_HEADER_ENGLISH_MAX_WORDS
        and len(left_text) <= _TABLE_HEADER_ENGLISH_MAX_CHARACTERS
        and len(right_text) <= _TABLE_HEADER_ENGLISH_MAX_CHARACTERS
    ):
        left_is_descriptor = bool(left_terms & _TABLE_HEADER_ENGLISH_DESCRIPTOR_TERMS)
        right_is_descriptor = bool(right_terms & _TABLE_HEADER_ENGLISH_DESCRIPTOR_TERMS)
        left_is_value = bool(left_terms & _TABLE_HEADER_ENGLISH_VALUE_TERMS)
        right_is_value = bool(right_terms & _TABLE_HEADER_ENGLISH_VALUE_TERMS)
        if (left_is_descriptor and right_is_value) or (
            right_is_descriptor and left_is_value
        ):
            return True
    # CJK 没有可靠空格分词：只接受短标签且两栏角色互补的“字段 | 值”首行形态。
    if (
        len(left_text) > _TABLE_HEADER_CJK_MAX_CHARACTERS
        or len(right_text) > _TABLE_HEADER_CJK_MAX_CHARACTERS
    ):
        return False
    left_is_descriptor = left_text.startswith(tuple(_TABLE_HEADER_CJK_DESCRIPTOR_PREFIXES))
    right_is_descriptor = right_text.startswith(tuple(_TABLE_HEADER_CJK_DESCRIPTOR_PREFIXES))
    left_is_value = left_text.startswith(tuple(_TABLE_HEADER_CJK_VALUE_PREFIXES))
    right_is_value = right_text.startswith(tuple(_TABLE_HEADER_CJK_VALUE_PREFIXES))
    return (left_is_descriptor and right_is_value) or (
        right_is_descriptor and left_is_value
    )


def _has_repeated_numeric_value_grid(
    left_by_top: dict[float, str],
    right_by_top: dict[float, str],
    common_tops: list[float],
) -> bool:
    """Return True for a dual-column numeric/unit grid that this lightweight path will not reorder."""

    # 只计同一 y 基线的值行；无论值在左、右或两侧，都没有足够结构证据证明安全的列优先阅读顺序。
    value_rows = sum(
        bool(_NUMERIC_UNIT_VALUE_ANYWHERE_RE.search(left_by_top[top]))
        or bool(_NUMERIC_UNIT_VALUE_ANYWHERE_RE.search(right_by_top[top]))
        for top in common_tops
    )
    return value_rows >= _NUMERIC_VALUE_GRID_MIN_ROWS


def _looks_like_continuous_prose_line(text: str) -> bool:
    """Require multi-word or substantial no-space Unicode text before column reordering."""

    # `[^\\W\\d_]` 在 Unicode 模式下只匹配字母：既支持中文等无空格文字，也排除数字、下划线和标点。
    letter_runs = re.findall(r"[^\W\d_]+", normalize_line(text), flags=re.UNICODE)
    # 有至少两个文字段可覆盖空格分词语言；单一长文字段覆盖中文等无空格正文。
    return len(letter_runs) >= 2 or any(
        len(letter_run) >= _CONTINUOUS_NO_SPACE_TEXT_MIN_LETTERS
        for letter_run in letter_runs
    )


def _words_to_visual_line(words: list[dict[str, float | str]]) -> str:
    """按词的可见 x 坐标重建一条栏内文字行，不依赖底层返回顺序。"""

    ordered_words = sorted(
        words,
        key=lambda word: (
            float(word["x0"]),
            float(word["x1"]),
            str(word["text"]),
        ),
    )
    return " ".join(str(word["text"]) for word in ordered_words)


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



def _filtered_layout_page(
    page: object,
    *,
    coordinate_words: list[dict[str, object]] | None = None,
    gutter_boxes: tuple[tuple[float, float, float, float], ...] | None = None,
    footer_boxes: tuple[tuple[float, float, float, float], ...] | None = None,
    header_boxes: tuple[tuple[float, float, float, float], ...] | None = None,
) -> object:
    """Return a page view with only spatially proven gutters/watermarks removed."""

    working_page = page
    watermark_keys = _draft_watermark_object_keys(working_page)
    gutter_boxes = gutter_boxes if gutter_boxes is not None else ()  # 单页局部几何无法排除合法长列表，默认必须保留。
    footer_boxes = footer_boxes if footer_boxes is not None else _proven_running_footer_boxes(
        working_page,
        words=coordinate_words,
    )
    header_boxes = header_boxes if header_boxes is not None else _proven_running_header_boxes(
        working_page,
        words=coordinate_words,
    )
    if not watermark_keys and not gutter_boxes and not footer_boxes and not header_boxes:
        return working_page  # 没有完整空间证据时一律保留。
    try:
        return working_page.filter(
            lambda obj: (
                _layout_object_key(obj) not in watermark_keys
                and not _layout_object_inside_any_box(obj, gutter_boxes, digits_only=True)
                and not _layout_object_inside_any_box(obj, footer_boxes)
                and not _layout_object_inside_any_box(obj, header_boxes)
            )
        )  # 原始 DocumentBlock 保留来源事实；比较文本只剔除已证明的边栏和水印字形。
    except Exception:
        return working_page  # 过滤接口异常时优先保留内容，避免静默漏报差异。


def _document_proven_line_number_gutter_boxes(
    pages: list[tuple[int, object]],
    words_by_page: dict[int, list[dict[str, float | str]]],
    *,
    coordinate_issues_by_page: dict[
        int,
        tuple[list[str], str | None],
    ] | None = None,
) -> dict[int, tuple[tuple[float, float, float, float], ...]]:
    """Return edge boxes only for a document-wide printed 1..49 line grid.

    A long aligned list is still semantic content.  Destructive comparison-text
    filtering therefore needs independent evidence that lists do not provide:
    at least three pages, high selected-page coverage, a nearly complete 1..49
    reset on every supporting page, stable vertical pitch/origin, and several
    numbered baselines that contain no body text at all.  Raw coordinate blocks
    are built before these boxes are applied, so the source evidence remains
    available for audit.
    """

    if len(pages) < _DOCUMENT_LINE_NUMBER_MIN_PAGES:
        return {}  # 单页或两页无法排除规则排版的合法长列表。

    evidence_by_page: dict[
        int,
        tuple[tuple[tuple[float, float, float, float], ...], float, float],
    ] = {}
    for page_number, page in pages:
        page_warnings, page_error = (coordinate_issues_by_page or {}).get(
            page_number,
            ([], None),
        )
        if not _coordinate_issues_preserve_line_number_grid_evidence(
            page_warnings,
            page_error,
        ):
            continue  # 坐标验证可能丢掉同基线正文；未知缺失会伪造空白行，必须 fail-closed。
        page_words = words_by_page.get(page_number, [])
        page_evidence = _page_printed_line_number_grid_evidence(
            page,
            words=page_words,
        )
        if len(page_evidence) == 1:
            evidence_by_page[page_number] = page_evidence[0]
        # 同页出现多个同等强候选列时语义仍不唯一，保守地不给该页过滤权。

    minimum_support = max(
        _DOCUMENT_LINE_NUMBER_MIN_PAGES,
        math.ceil(len(pages) * _DOCUMENT_LINE_NUMBER_MIN_PAGE_COVERAGE),
    )
    if len(evidence_by_page) < minimum_support:
        return {}  # 只在少数页出现的数字列更像正文列表或表格。

    median_origin = median(item[1] for item in evidence_by_page.values())
    median_pitch = median(item[2] for item in evidence_by_page.values())
    stable_evidence = {
        page_number: evidence
        for page_number, evidence in evidence_by_page.items()
        if abs(evidence[1] - median_origin)
        <= _DOCUMENT_LINE_NUMBER_GRID_ORIGIN_TOLERANCE
        and abs(evidence[2] - median_pitch)
        <= _DOCUMENT_LINE_NUMBER_GRID_PITCH_TOLERANCE
    }
    if len(stable_evidence) < minimum_support:
        return {}  # 跨页网格的起点或行距不稳定，不足以证明是固定打印行号。

    return {
        page_number: evidence[0]
        for page_number, evidence in stable_evidence.items()
    }  # 只返回网格中实际参与证明的数字词 bbox；列带内其他技术数值不得被带走。


def _coordinate_issues_preserve_line_number_grid_evidence(
    warnings: list[str],
    error: str | None,
) -> bool:
    """Allow only a few explicit duplicate-word removals in otherwise complete evidence."""

    if error is not None or len(warnings) > 4:
        return False
    return all("跳过重复坐标词" in warning for warning in warnings)
    # 重复词已由相同坐标和文本证明为冗余；其他任何坐标缺失仍会撤销 orphan-baseline 证据。


def _page_printed_line_number_grid_evidence(
    page: object,
    *,
    words: list[dict[str, object]],
) -> list[
    tuple[tuple[tuple[float, float, float, float], ...], float, float]
]:
    """Return high-confidence printed-grid evidence for one page.

    Each result is ``(word_boxes, normalized_origin, normalized_pitch)``.
    Side is not part of the signature because facing-page masters legitimately
    alternate the same print grid between left and right outer margins.
    """

    width = float(getattr(page, "width", 0) or 0)
    height = float(getattr(page, "height", 0) or 0)
    if width <= 0 or height <= 0:
        return []
    evidence: list[
        tuple[tuple[tuple[float, float, float, float], ...], float, float]
    ] = []
    for _side, cluster in _candidate_line_number_gutter_clusters(
        page,
        words=words,
    ):
        metrics = _printed_line_number_grid_metrics(
            cluster,
            all_words=words,
            page_width=width,
            page_height=height,
        )
        if metrics is None:
            continue
        word_boxes, origin, pitch = metrics
        evidence.append((word_boxes, origin / height, pitch / height))
    return evidence


def _printed_line_number_grid_metrics(
    cluster: list[dict[str, object]],
    *,
    all_words: list[dict[str, object]],
    page_width: float,
    page_height: float,
) -> tuple[tuple[tuple[float, float, float, float], ...], float, float] | None:
    """Validate a near-complete, evenly spaced 1..49 grid with blank rows."""

    value_words: dict[int, list[dict[str, object]]] = {}
    for word in cluster:
        text = str(word.get("text", "")).strip()
        if not text.isdigit():
            return None
        value = int(text)
        if not 1 <= value <= _DOCUMENT_LINE_NUMBER_LAST_VALUE:
            return None  # 同一窄列混入 50+技术值时，它就不是可安全删除的 1..49 网格。
        value_words.setdefault(value, []).append(word)
    values = sorted(value_words)
    if (
        len(values) < _DOCUMENT_LINE_NUMBER_MIN_DISTINCT_VALUES
        or not values
        or values[0] != 1
        or values[-1] != _DOCUMENT_LINE_NUMBER_LAST_VALUE
    ):
        return None  # 起点、终点和覆盖率共同证明每页独立重置。

    singleton_centers = {
        value: (
            float(candidates[0]["top"]) + float(candidates[0]["bottom"])
        )
        / 2.0
        for value, candidates in value_words.items()
        if len(candidates) == 1
    }
    singleton_values = sorted(singleton_centers)
    if (
        len(singleton_values) < _DOCUMENT_LINE_NUMBER_MIN_SINGLETON_ANCHORS
        or singleton_values[-1] - singleton_values[0] < _LINE_NUMBER_MIN_RUN
    ):
        return None  # 候选值大面积重复时无法判断哪一列才是真网格，必须保留原文。
    unit_pitches = [
        (singleton_centers[current] - singleton_centers[previous])
        / (current - previous)
        for previous, current in zip(
            singleton_values,
            singleton_values[1:],
            strict=False,
        )
        if current > previous
    ]
    if not unit_pitches:
        return None
    pitch = median(unit_pitches)
    if not page_height * 0.01 <= pitch <= page_height * 0.03:
        return None  # 排除极密坐标轴刻度和稀疏编号段。
    origin = median(
        singleton_centers[value] - (value - 1) * pitch
        for value in singleton_values
    )
    selected_words = {
        value: min(
            candidates,
            key=lambda word: abs(
                (float(word["top"]) + float(word["bottom"])) / 2.0
                - (origin + (value - 1) * pitch)
            ),
        )
        for value, candidates in value_words.items()
    }  # 正文技术数与行号同值时，只让贴合唯一锚点网格的那个物理词参与证明。
    centers = {
        value: (
            float(selected_words[value]["top"])
            + float(selected_words[value]["bottom"])
        )
        / 2.0
        for value in values
    }
    selected_unit_pitches = [
        (centers[current] - centers[previous]) / (current - previous)
        for previous, current in zip(values, values[1:], strict=False)
        if current > previous
    ]
    pitch = median(selected_unit_pitches)
    origin = median(centers[value] - (value - 1) * pitch for value in values)
    maximum_residual = max(
        abs(centers[value] - (origin + (value - 1) * pitch))
        for value in values
    )
    if maximum_residual > max(1.5, pitch * 0.18):
        return None  # 近完整的值域还必须落在单一线性行网格上。

    box = (
        min(float(word["x0"]) for word in cluster),
        min(float(word["top"]) for word in cluster),
        max(float(word["x1"]) for word in cluster),
        max(float(word["bottom"]) for word in cluster),
    )
    if box[2] - box[0] > max(18.0, page_width * 0.035):
        return None  # 一个真正的行号列只占窄带，不能把宽表格数字区一并授权。

    cluster_word_ids = {id(word) for word in cluster}
    baseline_tolerance = max(2.5, min(5.0, pitch * 0.36))
    orphan_baselines = 0
    proven_grid_words: list[dict[str, object]] = []
    for value in values:
        number_word = selected_words[value]
        number_center = (
            float(number_word["top"]) + float(number_word["bottom"])
        ) / 2.0
        proven_grid_words.append(number_word)  # 只有贴合预期网格位置的这一个词获得过滤权。
        has_body_on_baseline = any(
            id(word) not in cluster_word_ids
            and bool(str(word.get("text", "")).strip())
            and abs(
                (float(word.get("top", 0) or 0) + float(word.get("bottom", 0) or 0))
                / 2.0
                - number_center
            )
            <= baseline_tolerance
            for word in all_words
        )
        if not has_body_on_baseline:
            orphan_baselines += 1
    if orphan_baselines < _DOCUMENT_LINE_NUMBER_MIN_ORPHAN_BASELINES:
        return None  # 每个数字都有同基线正文时，它仍可能是合法编号列表。
    split_fragment_boxes = _split_missing_grid_number_fragment_boxes(
        missing_values=set(range(1, _DOCUMENT_LINE_NUMBER_LAST_VALUE + 1))
        - set(values),
        selected_words=selected_words,
        all_words=all_words,
        origin=origin,
        pitch=pitch,
        page_width=page_width,
    )
    word_boxes = tuple(
        (
            float(word["x0"]),
            float(word["top"]),
            float(word["x1"]),
            float(word["bottom"]),
        )
        for word in proven_grid_words
    ) + split_fragment_boxes
    return word_boxes, origin, pitch  # 完整词与严格重建的拆分数字均只按各自字形 bbox 删除。


def _split_missing_grid_number_fragment_boxes(
    *,
    missing_values: set[int],
    selected_words: dict[int, dict[str, object]],
    all_words: list[dict[str, object]],
    origin: float,
    pitch: float,
    page_width: float,
) -> tuple[tuple[float, float, float, float], ...]:
    """Recover a missing two-digit grid label only from two exact edge fragments."""

    if not missing_values or not selected_words:
        return ()
    selected_ids = {id(word) for word in selected_words.values()}
    column_left = min(float(word["x0"]) for word in selected_words.values())
    column_right = max(float(word["x1"]) for word in selected_words.values())
    x_tolerance = max(1.0, page_width * 0.003)
    center_tolerance = max(0.8, min(1.8, pitch * 0.12))
    recovered: list[tuple[float, float, float, float]] = []
    for value in sorted(missing_values):
        if value < 10:
            continue  # 1..9 不存在可证明的双字形拆分形式。
        expected_center = origin + (value - 1) * pitch
        fragments: list[dict[str, object]] = []
        for word in all_words:
            text = str(word.get("text", "")).strip()
            if id(word) in selected_ids or not re.fullmatch(r"\d", text):
                continue
            try:
                x0 = float(word["x0"])
                x1 = float(word["x1"])
                top = float(word["top"])
                bottom = float(word["bottom"])
            except (KeyError, TypeError, ValueError):
                continue
            center = (top + bottom) / 2.0
            if (
                column_left - x_tolerance <= x0
                and x1 <= column_right + x_tolerance
                and abs(center - expected_center) <= center_tolerance
            ):
                fragments.append(word)
        fragments.sort(key=lambda word: (float(word["x0"]), float(word["x1"])))
        if len(fragments) != 2:
            continue
        first, second = fragments
        combined = f'{str(first["text"]).strip()}{str(second["text"]).strip()}'
        horizontal_gap = float(second["x0"]) - float(first["x1"])
        centers = [
            (float(word["top"]) + float(word["bottom"])) / 2.0
            for word in fragments
        ]
        if (
            combined != str(value)
            or max(centers) - min(centers) > center_tolerance
            or not -0.5 <= horizontal_gap <= x_tolerance
            or abs(float(first["x0"]) - column_left) > x_tolerance
            or abs(float(second["x1"]) - column_right) > x_tolerance
        ):
            continue
        recovered.extend(
            (
                float(word["x0"]),
                float(word["top"]),
                float(word["x1"]),
                float(word["bottom"]),
            )
            for word in fragments
        )
    return tuple(recovered)


def _candidate_line_number_gutter_boxes(
    page: object,
    *,
    words: list[dict[str, object]] | None = None,
) -> tuple[tuple[float, float, float, float], ...]:
    """Return page-local numeric-column candidates for document-level proof."""

    return tuple(
        (
            min(float(word["x0"]) for word in cluster),
            min(float(word["top"]) for word in cluster),
            max(float(word["x1"]) for word in cluster),
            max(float(word["bottom"]) for word in cluster),
        )
        for _side, cluster in _candidate_line_number_gutter_clusters(
            page,
            words=words,
        )
    )


def _candidate_line_number_gutter_clusters(
    page: object,
    *,
    words: list[dict[str, object]] | None = None,
) -> list[tuple[str, list[dict[str, object]]]]:
    """Return page-local candidate clusters with their physical edge."""

    if words is None:
        try:
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False) or []
        except Exception:
            return []
    width = float(getattr(page, "width", 0) or 0)
    height = float(getattr(page, "height", 0) or 0)
    if width <= 0 or height <= 0:
        return []
    proven_clusters: list[tuple[str, list[dict[str, object]]]] = []
    for side in ("left", "right"):
        candidates = [
            word
            for word in words
            if _is_gutter_line_number(word, width, height, side=side)
        ]
        for cluster in _cluster_gutter_candidates(candidates, width, side=side):  # 每个窄 x 列独立证明连续行号。
            if not _looks_like_line_number_sequence(cluster, height):
                continue  # 同侧正文数字即使与真实行号共存，也不能借用另一列的序列证据。
            proven_clusters.append((side, cluster))  # 窄带 bbox 由调用方按本簇独立计算。
    return proven_clusters


def _candidate_line_number_gutter_sides(
    page: object,
    *,
    words: list[dict[str, object]] | None = None,
) -> tuple[str, ...]:
    """Return the page edges containing an ambiguous long numeric column."""

    width = float(getattr(page, "width", 0) or 0)
    if width <= 0:
        return ()
    sides = {
        "left" if (left + right) / 2.0 <= width / 2.0 else "right"
        for left, _top, right, _bottom in _candidate_line_number_gutter_boxes(
            page,
            words=words,
        )
    }
    return tuple(side for side in ("left", "right") if side in sides)


def _cluster_gutter_candidates(
    words: list[dict[str, object]],
    width: float,
    *,
    side: str,
) -> list[list[dict[str, object]]]:
    """Group numeric edge words by the aligned inner edge of a real gutter column."""

    anchor_key = "x1" if side == "left" else "x0"  # 左行号通常右对齐，右行号通常左对齐。
    tolerance = max(2.0, width * _LINE_NUMBER_COLUMN_TOLERANCE_RATIO)  # 兼容 PDF 字距抖动但不跨入正文起点。
    clusters: list[list[dict[str, object]]] = []  # 每组候选共享同一条窄行号列。
    for word in sorted(words, key=lambda item: float(item[anchor_key])):  # 横向排序让相邻列可稳定分组。
        anchor = float(word[anchor_key])  # 内侧边缘比词中心更能兼容一位数和两位数宽度。
        if not clusters:
            clusters.append([word])  # 第一个候选建立首个待证明列。
            continue
        current = clusters[-1]  # 排序后只需与最近的横向列比较。
        current_anchor = sum(float(item[anchor_key]) for item in current) / len(current)  # 用列均值抵抗轻微坐标抖动。
        if abs(anchor - current_anchor) <= tolerance:
            current.append(word)  # 坐标落在容差内，加入同一行号列候选。
        else:
            clusters.append([word])  # 与既有列距离过大时单独建组，正文数字不能共享序列证明。
    return clusters  # 调用方仍需对每组验证数量、连续值和垂直跨度。


def _proven_running_footer_boxes(
    page: object,
    *,
    words: list[dict[str, object]] | None = None,
) -> tuple[tuple[float, float, float, float], ...]:
    """Return a bottom-margin box for a complete running-footer signature.

    The same page footer may be interleaved into chart text by reading order.
    Remove it before text extraction only when its URL is in the bottom margin
    and the nearby lines also prove a copyright, draft, or clause footer.
    """

    if words is None:
        try:
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False) or []
        except Exception:
            return ()
    width = float(getattr(page, "width", 0) or 0)
    height = float(getattr(page, "height", 0) or 0)
    if width <= 0 or height <= 0:
        return ()

    bottom_start = height * 0.82
    bottom_words = [
        word
        for word in words
        if float(word.get("top", -1)) >= bottom_start
    ]
    if not bottom_words:
        return ()
    bottom_lines = _word_lines_with_bounds(bottom_words)
    marker_lines = [
        line for line in bottom_lines if _looks_like_running_footer_marker(line[2])
    ]
    url_lines = [
        line
        for line in bottom_lines
        if re.search(r"(?:https?://|www\.)\S+", line[2])
        and len(line[2]) <= 180
    ]
    clustered_url_lines = [
        line
        for line in url_lines
        if any(abs(line[0] - marker[0]) <= 48.0 for marker in marker_lines)
    ]
    if not marker_lines or not clustered_url_lines:
        return ()

    footer_tops = [line[0] for line in [*marker_lines, *clustered_url_lines]]
    return ((0.0, min(footer_tops), width, height),)


def _looks_like_running_footer_marker(line_text: str) -> bool:
    """Recognize a short legal/status/title line that can anchor a footer cluster."""

    candidate = normalize_line(line_text).casefold()
    if re.match(r"^(?:copyright\b|©)", candidate):
        return True
    if "draft" in candidate and any(
        marker in candidate
        for marker in ("watermark", "not to be shared", "not for distribution", "publication", "approval")
    ):
        return True
    return (
        bool(re.search(r"\b(?:clause|chapter|section|part)\s+\d", candidate))
        and bool(re.search(r"\s[-–—|]\s", candidate))
        and not re.search(r"\b(?:shall|must|should|required|prohibited)\b", candidate)
    )


def _proven_running_header_boxes(
    page: object,
    *,
    words: list[dict[str, object]] | None = None,
) -> tuple[tuple[float, float, float, float], ...]:
    """Return only a complete implementation-agreement title in the top margin."""

    if words is None:
        try:
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False) or []
        except Exception:
            return ()
    width = float(getattr(page, "width", 0) or 0)
    height = float(getattr(page, "height", 0) or 0)
    if width <= 0 or height <= 0:
        return ()
    top_words = [word for word in words if float(word.get("bottom", height + 1)) <= height * 0.18]
    boxes: list[tuple[float, float, float, float]] = []
    for top, bottom, line_text in _word_lines_with_bounds(top_words):
        if (
            "implementation agreement" in line_text
            and re.search(r"\b(?:interface|protocol|specification)\b|\bi/o\b", line_text)
        ):
            boxes.append((0.0, top, width, bottom))
    return tuple(boxes)


def _word_lines_with_bounds(words: list[dict[str, object]]) -> list[tuple[float, float, str]]:
    """Return stable visual lines with their vertical span for coordinate-only filters."""

    lines: list[tuple[float, list[dict[str, object]]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        if lines and abs(top - lines[-1][0]) <= 1.5:
            lines[-1][1].append(word)
        else:
            lines.append((top, [word]))
    return [
        (
            top,
            max(float(word["bottom"]) for word in line_words),
            " ".join(
                str(word.get("text", ""))
                for word in sorted(line_words, key=lambda item: float(item["x0"]))
            ).casefold(),
        )
        for top, line_words in lines
    ]


def _layout_object_inside_any_box(
    obj: dict[str, object],
    boxes: tuple[tuple[float, float, float, float], ...],
    *,
    digits_only: bool = False,
) -> bool:
    """Return whether a PDF character lies inside a proven filtered-layout box."""

    if not boxes or (digits_only and not str(obj.get("text", "")).isdigit()):
        return False
    try:
        x0 = float(obj["x0"])
        top = float(obj["top"])
        x1 = float(obj["x1"])
        bottom = float(obj["bottom"])
    except (KeyError, TypeError, ValueError):
        return False
    tolerance = 0.5
    return any(
        x0 >= left - tolerance
        and x1 <= right + tolerance
        and top >= upper - tolerance
        and bottom <= lower + tolerance
        for left, upper, right, lower in boxes
    )


def _layout_object_center_inside_any_box(
    obj: dict[str, object],
    boxes: tuple[tuple[float, float, float, float], ...],
) -> bool:
    """Assign table glyphs by centre so lowered scripts crossing rules are removed."""

    if not boxes:
        return False
    try:
        x_center = (float(obj["x0"]) + float(obj["x1"])) / 2
        y_center = (float(obj["top"]) + float(obj["bottom"])) / 2
    except (KeyError, TypeError, ValueError):
        return False
    tolerance = 0.5
    return any(
        left - tolerance <= x_center <= right + tolerance
        and upper - tolerance <= y_center <= lower + tolerance
        for left, upper, right, lower in boxes
    )


def _filter_coordinate_words_for_layout_noise(
    words: list[dict[str, object]],
    *,
    gutter_boxes: tuple[tuple[float, float, float, float], ...],
    footer_boxes: tuple[tuple[float, float, float, float], ...],
    header_boxes: tuple[tuple[float, float, float, float], ...],
) -> list[dict[str, object]]:
    """Keep text-block evidence consistent with the filtered comparison page."""

    return [
        word
        for word in words
        if not _layout_object_inside_any_box(word, gutter_boxes, digits_only=True)
        and not _layout_object_inside_any_box(word, footer_boxes)
        and not _layout_object_inside_any_box(word, header_boxes)
    ]


def _page_has_non_linear_reading_order(page: object) -> bool:
    """Return True when coordinate words provide conservative two-column evidence."""

    layout_risk, _warning, _insufficient, _coverage = _assess_page_reading_order(page)
    return layout_risk


def _assess_page_reading_order(
    page: object,
    *,
    words: list[dict[str, float | str]] | None = None,
    coordinate_error: str | None = None,
) -> tuple[bool, str | None, bool, int | None]:
    """Return reading-order risk, failure detail, and coordinate coverage facts."""

    if coordinate_error is not None:
        return False, coordinate_error, False, None  # 调用方已尝试过坐标提取，禁止为风险检查重复调用页面 API。
    if words is None:
        try:
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False) or []
        except Exception as exc:
            return False, f"无法提取坐标词: {exc}", False, None
    word_lines = _visual_word_lines(words)
    coordinate_text_characters = sum(
        _word_text_weight(word_line) for word_line in word_lines
    )
    if len(word_lines) < 2:
        return False, None, True, coordinate_text_characters

    x_start, x_end = _page_horizontal_bounds(page)
    page_width = x_end - x_start
    page_height = float(getattr(page, "height", 0) or 0)
    if page_width <= 0 or page_height <= 0:
        return False, "页面坐标尺寸无效", False, coordinate_text_characters
    minimum_gap = max(30.0, page_width * 0.08)
    short_page = len(word_lines) <= 3
    minimum_vertical_span = (
        max(12.0, page_height * 0.02)
        if short_page
        else max(36.0, page_height * 0.06)
    )
    short_text_minimum_vertical_span = max(
        minimum_vertical_span * 3,
        page_height * _COLUMN_SHORT_MIN_PAGE_SPAN_RATIO,
    )
    for fraction in _COLUMN_GUTTER_FRACTIONS:
        gutter_x = x_start + page_width * fraction
        supporting_tops: list[float] = []
        short_text_supporting_tops: list[float] = []
        for word_line in word_lines:
            left_words = [word for word in word_line if float(word["x1"]) <= gutter_x]
            right_words = [word for word in word_line if float(word["x0"]) >= gutter_x]
            if not left_words or not right_words:
                continue
            if any(float(word["x0"]) < gutter_x < float(word["x1"]) for word in word_line):
                continue  # 横跨中心的普通正文直接否定这一视觉行的 gutter 证据。
            left_weight = _word_text_weight(left_words)
            right_weight = _word_text_weight(right_words)
            if (
                left_weight < _COLUMN_SHORT_TEXT_WEIGHT
                or right_weight < _COLUMN_SHORT_TEXT_WEIGHT
            ):
                continue  # 极短数字/符号单元格不足以证明左右两栏都是正文。
            left_edge = max(float(word["x1"]) for word in left_words)
            right_edge = min(float(word["x0"]) for word in right_words)
            if right_edge - left_edge < minimum_gap:
                continue
            top = min(float(word["top"]) for word in word_line)
            short_text_supporting_tops.append(top)
            if (
                left_weight >= _COLUMN_STRONG_TEXT_WEIGHT
                and right_weight >= _COLUMN_STRONG_TEXT_WEIGHT
            ):
                supporting_tops.append(top)
        strong_minimum_supporting_rows = 2  # 两行两侧均有 >=12 字符且存在宽 gutter，证据不应被无关标题/页脚反转。
        strong_minimum_vertical_span = max(12.0, page_height * 0.02)
        strong_aligned_support = (
            len(supporting_tops) >= strong_minimum_supporting_rows
            and max(supporting_tops) - min(supporting_tops)
            >= strong_minimum_vertical_span
        )
        short_aligned_support = (
            len(short_text_supporting_tops) >= _COLUMN_SHORT_MIN_SUPPORTING_LINES
            and max(short_text_supporting_tops)
            - min(short_text_supporting_tops)
            >= short_text_minimum_vertical_span
        )
        aligned_support_tops = (
            supporting_tops if strong_aligned_support else short_text_supporting_tops
        )
        if (
            (strong_aligned_support or short_aligned_support)
            and not _has_local_grid_evidence(
                page,
                aligned_support_tops,
                gutter_x=gutter_x,
                minimum_gap=minimum_gap,
            )
        ):
            return True, None, False, coordinate_text_characters
        if _has_staggered_column_support(
            page,
            word_lines,
            gutter_x=gutter_x,
            minimum_gap=minimum_gap,
            minimum_vertical_span=minimum_vertical_span,
            short_text_minimum_vertical_span=short_text_minimum_vertical_span,
        ):
            return True, None, False, coordinate_text_characters
    return False, None, False, coordinate_text_characters


def _has_staggered_column_support(
    page: object,
    word_lines: list[list[dict[str, float | str]]],
    *,
    gutter_x: float,
    minimum_gap: float,
    minimum_vertical_span: float,
    short_text_minimum_vertical_span: float,
) -> bool:
    """Detect left/right prose columns whose baselines are intentionally offset."""

    left_tops: list[float] = []
    right_tops: list[float] = []
    short_text_left_tops: list[float] = []
    short_text_right_tops: list[float] = []
    half_gap = minimum_gap / 2
    for word_line in word_lines:
        if any(float(word["x0"]) < gutter_x < float(word["x1"]) for word in word_line):
            continue
        left_words = [word for word in word_line if float(word["x1"]) <= gutter_x]
        right_words = [word for word in word_line if float(word["x0"]) >= gutter_x]
        if left_words and right_words:
            continue  # 同一基线两侧都有内容时交给上面的对齐栏逻辑，避免把网格表格算两次。
        top = min(float(word["top"]) for word in word_line)
        if (
            left_words
            and _word_text_weight(left_words) >= _COLUMN_SHORT_TEXT_WEIGHT
            and gutter_x - max(float(word["x1"]) for word in left_words) >= half_gap
        ):
            short_text_left_tops.append(top)
            if _word_text_weight(left_words) >= _COLUMN_STRONG_TEXT_WEIGHT:
                left_tops.append(top)
        elif (
            right_words
            and _word_text_weight(right_words) >= _COLUMN_SHORT_TEXT_WEIGHT
            and min(float(word["x0"]) for word in right_words) - gutter_x >= half_gap
        ):
            short_text_right_tops.append(top)
            if _word_text_weight(right_words) >= _COLUMN_STRONG_TEXT_WEIGHT:
                right_tops.append(top)
    if (
        len(short_text_left_tops) >= _COLUMN_SHORT_MIN_SUPPORTING_LINES
        and len(short_text_right_tops) >= _COLUMN_SHORT_MIN_SUPPORTING_LINES
    ):
        short_overlap_start = max(
            min(short_text_left_tops),
            min(short_text_right_tops),
        )
        short_overlap_end = min(
            max(short_text_left_tops),
            max(short_text_right_tops),
        )
        if (
            short_overlap_end - short_overlap_start
            >= short_text_minimum_vertical_span
            and not _has_local_grid_evidence(
                page,
                [*short_text_left_tops, *short_text_right_tops],
                gutter_x=gutter_x,
                minimum_gap=minimum_gap,
            )
        ):
            return True
    minimum_column_lines = 2  # 强错行双栏同样不依赖页面还有多少全宽文字行。
    if len(left_tops) < minimum_column_lines or len(right_tops) < minimum_column_lines:
        return False
    overlap_start = max(min(left_tops), min(right_tops))
    overlap_end = min(max(left_tops), max(right_tops))
    required_overlap = 12.0
    return (
        overlap_end - overlap_start >= required_overlap
        and not _has_local_grid_evidence(
            page,
            [*left_tops, *right_tops],
            gutter_x=gutter_x,
            minimum_gap=minimum_gap,
        )
    )


def _has_local_grid_evidence(
    page: object,
    supporting_tops: list[float],
    *,
    gutter_x: float,
    minimum_gap: float,
) -> bool:
    """Return True when ruled objects overlap this candidate column region."""

    if not supporting_tops:
        return False
    band_start = min(supporting_tops) - 12.0
    band_end = max(supporting_tops) + 24.0
    half_gap = minimum_gap / 2
    crossing_rule_bounds: list[tuple[float, float]] = []
    for obj in [
        *(getattr(page, "lines", []) or []),
        *(getattr(page, "rects", []) or []),
    ]:
        bounds = _layout_object_bounds(obj, page)
        if bounds is None:
            continue
        x0, top, x1, bottom = bounds
        if bottom < band_start or top > band_end:
            continue
        if x0 <= gutter_x - half_gap and x1 >= gutter_x + half_gap:
            crossing_rule_bounds.append((top, bottom))
    if len(crossing_rule_bounds) < 4:
        return False
    ordered_support_tops = sorted(set(supporting_tops))
    supporting_gaps = sorted(
        right - left
        for left, right in zip(
            ordered_support_tops,
            ordered_support_tops[1:],
            strict=False,
        )
        if right > left
    )
    typical_supporting_gap = (
        supporting_gaps[len(supporting_gaps) // 2]
        if supporting_gaps
        else _GRID_RULE_CLUSTER_MIN_GAP
    )
    maximum_cluster_gap = max(
        _GRID_RULE_CLUSTER_MIN_GAP,
        typical_supporting_gap * _GRID_RULE_CLUSTER_LINE_GAP_MULTIPLIER,
    )
    clusters: list[list[tuple[float, float]]] = []
    for bounds in sorted(crossing_rule_bounds):
        if (
            not clusters
            or bounds[0] - max(bottom for _top, bottom in clusters[-1])
            > maximum_cluster_gap
        ):
            clusters.append([bounds])
        else:
            clusters[-1].append(bounds)
    for cluster in clusters:
        if len(cluster) < 4:
            continue
        grid_start = min(top for top, _bottom in cluster) - 12.0
        grid_end = max(bottom for _top, bottom in cluster) + 12.0
        covered_support_count = sum(
            grid_start <= top <= grid_end for top in supporting_tops
        )
        if (
            covered_support_count / len(supporting_tops)
            >= _GRID_MIN_TEXT_LINE_COVERAGE_RATIO
        ):
            return True
    return False


def _layout_object_bounds(
    obj: object,
    page: object,
) -> tuple[float, float, float, float] | None:
    """Return a pdfplumber line/rectangle bbox when its coordinates are usable."""

    def value(name: str) -> object | None:
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    try:
        x0 = float(value("x0"))
        x1 = float(value("x1"))
        top_value = value("top")
        bottom_value = value("bottom")
        if top_value is None or bottom_value is None:
            page_height = float(getattr(page, "height", 0) or 0)
            y0 = float(value("y0"))
            y1 = float(value("y1"))
            top = page_height - max(y0, y1)
            bottom = page_height - min(y0, y1)
        else:
            top = float(top_value)
            bottom = float(bottom_value)
    except (TypeError, ValueError):
        return None
    return min(x0, x1), min(top, bottom), max(x0, x1), max(top, bottom)


def _visual_word_lines(words: list[dict[str, object]]) -> list[list[dict[str, float | str]]]:
    """Group valid pdfplumber words into visual lines using their top coordinates."""

    valid_words: list[dict[str, float | str]] = []
    for word in words:
        try:
            text = normalize_line(str(word.get("text", "")))
            x0 = float(word["x0"])
            x1 = float(word["x1"])
            top = float(word["top"])
            bottom = float(word.get("bottom", top))
        except (KeyError, TypeError, ValueError):
            continue
        if text and x1 > x0:
            valid_words.append({"text": text, "x0": x0, "x1": x1, "top": top, "bottom": bottom})
    valid_words.sort(key=lambda word: (float(word["top"]), float(word["x0"])))

    lines: list[list[dict[str, float | str]]] = []
    for word in valid_words:
        if not lines or abs(float(word["top"]) - float(lines[-1][0]["top"])) > 3.0:
            lines.append([word])
        else:
            lines[-1].append(word)
    return lines


def _page_horizontal_bounds(page: object) -> tuple[float, float]:
    """Return coordinate bounds compatible with original and cropped pdfplumber pages."""

    bbox = tuple(getattr(page, "bbox", ()) or ())
    if len(bbox) == 4:
        try:
            return float(bbox[0]), float(bbox[2])
        except (TypeError, ValueError):
            pass
    return 0.0, float(getattr(page, "width", 0) or 0)


def _word_text_weight(words: list[dict[str, float | str]]) -> int:
    """Count visible letters and digits while ignoring punctuation-only cells."""

    return sum(len(re.sub(r"[^\w]+", "", str(word["text"]), flags=re.UNICODE)) for word in words)


def _content_x_bounds_without_line_gutters(page: object) -> tuple[float, float]:
    """Return full page bounds; numeric edge columns are observable content."""

    return _page_horizontal_bounds(page)


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

    values = sorted({int(str(word["text"]).strip()) for word in words})  # 去重后按数字顺序检查连续性。
    if len(values) < _LINE_NUMBER_MIN_COUNT:  # 重复抽取不能抬高证据；1..20 这类正文列表一律保留。
        return False
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
    )  # pdfplumber 的 upright 标志不能代表视觉旋转；应以文本变换矩阵判断。


def _draft_watermark_object_keys(page: object) -> frozenset[tuple[object, ...]]:
    """Return only characters proven to form a coherent full-page DRAFT mark."""

    try:
        page_characters = list(getattr(page, "chars", []) or [])
    except Exception:
        return frozenset()  # 坐标字符不可用时没有足够证据删除任何内容。
    candidates = [
        character
        for character in page_characters
        if isinstance(character, dict)
        and not _keep_non_watermark_object(character)
        and _layout_object_center(character) is not None
    ]
    if len(candidates) < len("DRAFT"):
        return frozenset()

    page_width = float(getattr(page, "width", 0) or 0)
    page_height = float(getattr(page, "height", 0) or 0)
    page_diagonal = math.hypot(page_width, page_height)
    if page_diagonal <= 0:
        return frozenset()

    proven: set[tuple[object, ...]] = set()
    for axis in (0, 1):
        ordered = sorted(
            candidates,
            key=lambda character: _layout_object_center(character)[axis],  # type: ignore[index]
        )
        for start in range(len(ordered) - len("DRAFT") + 1):
            cluster = ordered[start : start + len("DRAFT")]
            letters = "".join(str(character.get("text", "")).upper() for character in cluster)
            if letters not in {"DRAFT", "TFARD"}:
                continue
            if not _is_spatially_coherent_watermark_cluster(cluster, page_diagonal):
                continue
            proven.update(_layout_object_key(character) for character in cluster)
    return frozenset(proven)


def _is_spatially_coherent_watermark_cluster(
    characters: list[dict[str, object]],
    page_diagonal: float,
) -> bool:
    """Require one straight, evenly spaced and page-spanning five-letter run."""

    points = [_layout_object_center(character) for character in characters]
    if any(point is None for point in points):
        return False
    centers = [point for point in points if point is not None]
    start_x, start_y = centers[0]
    end_x, end_y = centers[-1]
    direction_x = end_x - start_x
    direction_y = end_y - start_y
    span = math.hypot(direction_x, direction_y)
    if span < page_diagonal * 0.15:
        return False  # 小型标签或局部图形不应被当成铺页水印。

    sizes = [float(character.get("size", 0) or 0) for character in characters]
    if not sizes or min(sizes) < max(sizes) * 0.60:
        return False  # 同一水印的字号应大致一致，防止把散落标题字母拼成一组。

    step_lengths: list[float] = []
    direction_length_squared = span * span
    maximum_off_axis_distance = max(page_diagonal * 0.04, max(sizes) * 0.35)
    for previous, current in zip(centers, centers[1:], strict=False):
        step_x = current[0] - previous[0]
        step_y = current[1] - previous[1]
        if step_x * direction_x + step_y * direction_y <= 0:
            return False  # 每个字母必须沿同一方向前进，不能在页面上来回跳跃。
        step_lengths.append(math.hypot(step_x, step_y))
    if not step_lengths or min(step_lengths) <= 0 or max(step_lengths) > min(step_lengths) * 2.5:
        return False

    for x, y in centers[1:-1]:
        off_axis_distance = abs(
            (x - start_x) * direction_y - (y - start_y) * direction_x
        ) / math.sqrt(direction_length_squared)
        if off_axis_distance > maximum_off_axis_distance:
            return False
    return True


def _layout_object_center(obj: dict[str, object]) -> tuple[float, float] | None:
    """Return a character bbox centre, or None when coordinate evidence is absent."""

    try:
        return (
            (float(obj["x0"]) + float(obj["x1"])) / 2,
            (float(obj["top"]) + float(obj["bottom"])) / 2,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _layout_object_key(obj: dict[str, object]) -> tuple[object, ...]:
    """Build a stable key for a filtered-page character object."""

    matrix = tuple(obj.get("matrix", ()) or ())
    return (
        obj.get("object_type"),
        str(obj.get("text", "")),
        float(obj.get("size", 0) or 0),
        float(obj.get("x0", 0) or 0),
        float(obj.get("x1", 0) or 0),
        float(obj.get("top", 0) or 0),
        float(obj.get("bottom", 0) or 0),
        matrix,
    )


def _is_rotated_text_object(obj: dict[str, object]) -> bool:
    """Return True when a PDF text object's transform has material rotation."""

    matrix = tuple(obj.get("matrix", ()) or ())  # PDF 文本矩阵前四项包含缩放、倾斜和旋转信息。
    if len(matrix) >= 4:
        try:
            a, b, c, d = (float(value) for value in matrix[:4])
        except (TypeError, ValueError):
            return not bool(obj.get("upright", True))  # 异常矩阵退回旧标志，保持兼容性。
        axis_scale = max(abs(a), abs(d), 1.0)  # 正常横排文字的 b/c 接近 0，a/d 表示主轴缩放。
        return max(abs(b), abs(c)) >= axis_scale * 0.15  # 明显非轴对齐文字才视作旋转，保留正常大标题。
    return not bool(obj.get("upright", True))  # 旧测试/手工对象没有矩阵时沿用 upright 语义。


def _extract_table_lines(page: object, pdf_name: str, page_number: int) -> tuple[list[str], list[str]]:
    """Extract tables from one filtered page and format them as review lines."""

    table_lines, _table_visuals, warnings, _covered_bboxes = _extract_table_lines_and_visuals(
        page,
        pdf_name,
        page_number,
    )  # 兼容旧测试和旧调用，只返回文本行和警告。
    return table_lines, warnings


def _extract_table_lines_and_visuals(
    page: object,
    pdf_name: str,
    page_number: int,
    *,
    geometry_words: list[dict[str, object]] | None = None,
) -> tuple[
    list[str],
    list[TableVisual],
    list[str],
    tuple[tuple[float, float, float, float], ...],
]:
    """Extract structured table rows and screenshot visual evidence."""

    try:
        table_objects = page.find_tables() or []
    except Exception as exc:
        return [], [], [f"{pdf_name}: 第 {page_number} 页表格定位失败: {exc}"], ()
    lines: list[str] = []  # 汇总该页所有表格行。
    visuals: list[TableVisual] = []  # 汇总该页所有表格截图和视觉识别摘要。
    warnings: list[str] = []  # 单页表格截图和 OCR 相关的非致命问题。
    fully_covered_bboxes: list[tuple[float, float, float, float]] = []
    geometry_words = geometry_words or []  # 复用页面唯一一次坐标观测，禁止表格路径再次调用 extract_words。
    for table_number, table in enumerate(table_objects, start=1):
        bbox = _table_bbox(table)  # 先取边界框，用表题和位置判断它是不是真表格。
        try:
            rows = table.extract() or []
        except Exception as exc:
            warnings.append(f"{pdf_name}: 第 {page_number} 页第 {table_number} 个表格行抽取失败: {exc}")
            rows = []
        cell_word_rows = _table_cell_word_rows(table, rows, geometry_words)
        source_rows = rows
        source_cell_word_rows = cell_word_rows
        rows, cell_word_rows = _split_geometry_proven_merged_table_column(
            rows,
            cell_word_rows,
        )
        (
            table_lines,
            row_content_lossless,
            row_alignment_reliable,
        ) = _table_lines_from_rows_with_evidence(
            rows,
            table_number,
            cell_word_rows=cell_word_rows,
        )  # 有字号和坐标时先恢复视觉上下标；证据缺失则沿用纯文字保守路径。
        title = _table_title_above_bbox(page, bbox) if bbox else ""  # 表题用于过滤图形误检和生成截图标题。
        if _should_skip_detected_table(title, table_lines):
            continue  # Figure/plot 或空伪表格不进入正文 diff，也不进入表格截图区。
        lines.extend(table_lines)  # 表格行保留在抽取文本中，后续有截图表格区时正文 diff 会自动去重隐藏。
        bbox_content_fully_represented = bool(
            bbox is not None
            and _table_bbox_content_is_fully_represented(
                page,
                bbox,
                source_rows,
            )
        )
        bbox_fully_represented = bool(
            bbox_content_fully_represented
            and row_content_lossless
            and row_alignment_reliable
        )
        if bbox_fully_represented:
            assert bbox is not None  # bool 门禁已证明 bbox 存在，仅帮助类型检查器收窄。
            fully_covered_bboxes.append(bbox)
        elif bbox is not None:
            fully_covered_bboxes.extend(
                _fully_represented_table_row_bboxes(
                    page,
                    table,
                    source_rows,
                    source_cell_word_rows,
                )
            )  # 整表含歧义行时，仍可逐行替换已完整结构化的记录，避免正确行重复进入正文卡片。
        if row_content_lossless and not row_alignment_reliable:
            warnings.append(
                f"{pdf_name}: 第 {page_number} 页第 {table_number} 个表格字符已完整纳入表格证据，"
                "但多行单元格的逻辑配对不确定；只有另外取得行对齐证明的原文才会从正文区去重，"
                "表格区保留聚合行和截图供复核。"
            )
        visual, visual_warning = _build_table_visual(
            page,
            table,
            table_lines,
            page_number,
            table_number,
            title=title,
            content_fully_represented=(
                bbox_content_fully_represented and row_content_lossless
            ),
            row_alignment_reliable=row_alignment_reliable,
        )  # 只为通过过滤的表格生成截图证据。
        if visual_warning:
            warnings.append(f"{pdf_name}: 第 {page_number} 页第 {table_number} 个表格截图生成失败: {visual_warning}")
        if visual is not None:
            visuals.append(visual)
    return lines, visuals, warnings, tuple(fully_covered_bboxes)


def _split_geometry_proven_merged_table_column(
    rows: list[list[object]],
    cell_word_rows: list[list[list[dict[str, object]]]] | None,
) -> tuple[
    list[list[object]],
    list[list[list[dict[str, object]]]] | None,
]:
    """Restore one detector-merged column from stable, lossless x-lane evidence.

    Some ruled PDFs omit or fragment one internal vertical stroke.  pdfplumber
    then returns two physical columns as one cell even though every body row has
    two widely separated x lanes.  Text alone is not enough to infer a missing
    boundary, so this repair requires repeated categorical/numeric body lanes,
    a matching two-part header, stable coordinates, and exact character
    conservation before changing the structured table.
    """

    if (
        cell_word_rows is None
        or len(rows) < 4
        or len(cell_word_rows) != len(rows)
        or not rows
    ):
        return rows, cell_word_rows
    widths = {len(row) for row in rows}
    if len(widths) != 1:
        return rows, cell_word_rows
    column_count = next(iter(widths))
    if column_count < 3 or any(len(word_row) != column_count for word_row in cell_word_rows):
        return rows, cell_word_rows

    candidates: list[tuple[int, float]] = []
    for column_index in range(column_count):
        split_intervals: list[tuple[float, float]] = []
        body_pairs: list[tuple[str, str]] = []
        valid = True
        for row_index in range(1, len(rows)):
            words = _valid_table_cell_words(cell_word_rows[row_index][column_index])
            split = _largest_visual_word_gap(words)
            if split is None:
                valid = False
                break
            left_edge, right_edge = split
            midpoint = (left_edge + right_edge) / 2
            left_words = [word for word in words if float(word["x1"]) <= midpoint]
            right_words = [word for word in words if float(word["x0"]) >= midpoint]
            if len(left_words) + len(right_words) != len(words):
                valid = False
                break
            left_text = _table_cell_text_from_visual_words(left_words)
            right_text = _table_cell_text_from_visual_words(right_words)
            if not (
                _looks_like_merged_column_category_lane(left_text)
                and (
                    _looks_like_pure_numeric_table_entry(right_text)
                    or _looks_like_missing_table_value(right_text)
                )
            ):
                valid = False
                break
            split_intervals.append((left_edge, right_edge))
            body_pairs.append((left_text, right_text))
        if not valid or len(split_intervals) < 3:
            continue
        common_left = max(interval[0] for interval in split_intervals)
        common_right = min(interval[1] for interval in split_intervals)
        if common_right - common_left < 24.0:
            continue
        split_x = (common_left + common_right) / 2
        header_words = _valid_table_cell_words(cell_word_rows[0][column_index])
        left_header_words = [word for word in header_words if float(word["x1"]) <= split_x]
        right_header_words = [word for word in header_words if float(word["x0"]) >= split_x]
        if len(left_header_words) + len(right_header_words) != len(header_words):
            continue
        left_header = _table_cell_text_from_visual_words(left_header_words)
        right_header = _table_cell_text_from_visual_words(right_header_words)
        if not (
            _looks_like_split_table_header_lane(left_header)
            and _looks_like_split_table_header_lane(right_header)
        ):
            continue
        reconstructed = [
            (left_header, right_header),
            *body_pairs,
        ]
        if any(
            _non_whitespace_character_counts(str(rows[row_index][column_index]))
            != _non_whitespace_character_counts(left + right)
            for row_index, (left, right) in enumerate(reconstructed)
        ):
            continue
        candidates.append((column_index, split_x))

    if len(candidates) != 1:
        return rows, cell_word_rows
    column_index, split_x = candidates[0]
    repaired_rows: list[list[object]] = []
    repaired_word_rows: list[list[list[dict[str, object]]]] = []
    for row, word_row in zip(rows, cell_word_rows, strict=True):
        words = _valid_table_cell_words(word_row[column_index])
        left_words = [word for word in words if float(word["x1"]) <= split_x]
        right_words = [word for word in words if float(word["x0"]) >= split_x]
        repaired_rows.append(
            [
                *row[:column_index],
                _table_cell_text_from_visual_words(left_words),
                _table_cell_text_from_visual_words(right_words),
                *row[column_index + 1 :],
            ]
        )
        repaired_word_rows.append(
            [
                *word_row[:column_index],
                left_words,
                right_words,
                *word_row[column_index + 1 :],
            ]
        )
    return repaired_rows, repaired_word_rows


def _valid_table_cell_words(
    words: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Return coordinate-complete non-watermark words from one table cell."""

    valid: list[dict[str, object]] = []
    for word in words:
        try:
            if (
                normalize_line(str(word.get("text", "")))
                and float(word["x1"]) > float(word["x0"])
                and _word_effective_size(word) < _WATERMARK_MIN_FONT_SIZE
            ):
                valid.append(word)
        except (KeyError, TypeError, ValueError):
            continue
    return valid


def _largest_visual_word_gap(
    words: list[dict[str, object]],
) -> tuple[float, float] | None:
    """Return one uniquely large horizontal word gap from a single body row."""

    if len(words) < 2:
        return None
    ordered = sorted(words, key=lambda word: (float(word["x0"]), float(word["x1"])))
    gaps = [
        (float(right["x0"]) - float(left["x1"]), float(left["x1"]), float(right["x0"]))
        for left, right in zip(ordered, ordered[1:], strict=False)
    ]
    positive = sorted((gap for gap in gaps if gap[0] > 0), reverse=True)
    if not positive or positive[0][0] < 36.0:
        return None
    if len(positive) > 1 and positive[0][0] < positive[1][0] * 2.5:
        return None
    return positive[0][1], positive[0][2]


def _table_cell_text_from_visual_words(words: list[dict[str, object]]) -> str:
    """Rebuild a cell while preserving its observed visual line boundaries."""

    return "\n".join(
        _words_to_visual_line(line)
        for line in _visual_word_lines(words)
        if line
    )


def _looks_like_split_table_header_lane(value: str) -> bool:
    """Require a compact field-like header on each side of a restored boundary."""

    candidate = normalize_line(value).casefold()
    if not candidate or len(candidate) > 64:
        return False
    terms = set(re.findall(r"[a-z]+", candidate))
    return bool(
        terms
        & {
            "channel",
            "condition",
            "description",
            "insertion",
            "loss",
            "maximum",
            "minimum",
            "mode",
            "parameter",
            "range",
            "symbol",
            "type",
            "unit",
            "units",
            "value",
        }
    )


def _looks_like_merged_column_category_lane(value: str) -> bool:
    """Recognize a compact categorical lane without treating prose as two columns."""

    candidate = normalize_line(value)
    return bool(
        1 <= len(candidate) <= 32
        and len(candidate.split()) <= 3
        and re.fullmatch(r"[A-Za-z][A-Za-z_./+() -]*", candidate)
        and not re.search(r"[.!?;:]$", candidate)
    )


def _table_cell_word_rows(
    table: object,
    rows: list[list[object]],
    geometry_words: list[dict[str, object]],
) -> list[list[list[dict[str, object]]]] | None:
    """Assign page words to table cells using pdfplumber's observed bboxes."""

    table_rows = list(getattr(table, "rows", ()) or ())
    if not geometry_words or len(table_rows) != len(rows):
        return None
    word_rows: list[list[list[dict[str, object]]]] = []
    for row_index, row in enumerate(rows):
        cells = list(getattr(table_rows[row_index], "cells", ()) or ())
        if len(cells) != len(row):
            return None
        word_rows.append(
            [
                [
                    word
                    for word in geometry_words
                    if cell_bbox
                    and _word_effective_size(word) < _WATERMARK_MIN_FONT_SIZE
                    and _layout_word_center_inside_box(word, tuple(cell_bbox))
                ]
                for cell_bbox in cells
            ]
        )
    return word_rows


def _layout_word_center_inside_box(
    word: dict[str, object],
    bbox: tuple[object, ...],
) -> bool:
    """Assign a word by its centre so lowered scripts may cross a cell border."""

    if len(bbox) != 4:
        return False
    try:
        x_center = (float(word["x0"]) + float(word["x1"])) / 2
        y_center = (float(word["top"]) + float(word["bottom"])) / 2
        left, upper, right, lower = (float(value) for value in bbox)
    except (KeyError, TypeError, ValueError):
        return False
    return left <= x_center <= right and upper <= y_center <= lower


def _table_bbox_is_fully_represented(
    page: object,
    bbox: tuple[float, float, float, float],
    rows: list[list[object]],
    *,
    row_content_fully_represented: bool,
    row_alignment_reliable: bool,
) -> bool:
    """Prove that a table bbox can be replaced by its structured rows."""

    if not rows or not row_content_fully_represented or not row_alignment_reliable:
        return False
    return _table_bbox_content_is_fully_represented(page, bbox, rows)


def _table_bbox_content_is_fully_represented(
    page: object,
    bbox: tuple[float, float, float, float],
    rows: list[list[object]],
) -> bool:
    """Prove exact bbox character coverage independently of logical row alignment."""

    if not rows:
        return False
    try:
        bbox_text = page.crop(bbox).extract_text(x_tolerance=1, y_tolerance=3) or ""
    except Exception:
        return False
    raw_text = "\n".join(
        str(cell)
        for row in rows
        for cell in row
        if cell is not None
    )
    bbox_characters = _non_whitespace_character_counts(bbox_text)
    raw_characters = _non_whitespace_character_counts(raw_text)
    return bool(bbox_characters and raw_characters == bbox_characters)


def _fully_represented_table_row_bboxes(
    page: object,
    table: object,
    rows: list[list[object]],
    cell_word_rows: list[list[list[dict[str, object]]]] | None,
) -> tuple[tuple[float, float, float, float], ...]:
    """Return only physical data-row boxes proven safe for structured replacement."""

    row_flags = _table_row_replacement_flags(rows, cell_word_rows)
    table_rows = list(getattr(table, "rows", ()) or ())
    if len(row_flags) != len(rows) or len(table_rows) != len(rows):
        return ()
    proven: list[tuple[float, float, float, float]] = []
    for row_index, (row, table_row, replaceable) in enumerate(
        zip(rows, table_rows, row_flags, strict=False)
    ):
        if not replaceable:
            continue
        try:
            bbox = tuple(float(value) for value in getattr(table_row, "bbox", ()) or ())
        except (TypeError, ValueError):
            continue
        try:
            cell_word_row = cell_word_rows[row_index] if cell_word_rows is not None else None
        except IndexError:
            cell_word_row = None
        if len(bbox) != 4 or not _table_row_bbox_matches_raw_cells(
            page,
            bbox,
            row,
            cell_word_row=cell_word_row,
        ):
            continue
        proven.append(bbox)
    return tuple(proven)


def _table_row_replacement_flags(
    rows: list[list[object]],
    cell_word_rows: list[list[list[dict[str, object]]]] | None,
) -> tuple[bool, ...]:
    """Mark physical rows whose structured expansion preserves cells and alignment."""

    repaired_rows = _rejoin_table_cell_subscripts(rows, cell_word_rows)
    indexed_rows = [
        (index, [_table_cell_lines(cell) for cell in row])
        for index, row in enumerate(repaired_rows)
    ]
    indexed_rows = [
        (index, row) for index, row in indexed_rows if any(any(cell) for cell in row)
    ]
    flags = [False] * len(rows)
    if not indexed_rows:
        return tuple(flags)
    raw_rows = [row for _index, row in indexed_rows]
    header_index = _find_table_header_row(raw_rows)
    if header_index is None:
        header = _default_header_for_table(raw_rows)
        data_rows = indexed_rows
    else:
        header = _clean_table_header(raw_rows[header_index])
        data_rows = indexed_rows[header_index + 1 :]
    for original_index, row in data_rows:
        cell_word_row = (
            cell_word_rows[original_index]
            if cell_word_rows is not None and original_index < len(cell_word_rows)
            else None
        )
        expanded_rows, expansion_alignment_reliable = _expand_table_row_with_evidence(
            row,
            header,
            cell_word_row=cell_word_row,
        )
        flags[original_index] = (
            _expanded_rows_preserve_source_cells(row, expanded_rows)
            and expansion_alignment_reliable
            and _expanded_rows_preserve_source_alignment(
                row,
                expanded_rows,
                cell_word_row=cell_word_row,
            )
            and any(
                _format_table_row(expanded_row, header, table_number=1)
                for expanded_row in expanded_rows
            )
        )
    return tuple(flags)


def _table_row_bbox_matches_raw_cells(
    page: object,
    bbox: tuple[float, ...],
    row: list[object],
    *,
    cell_word_row: list[list[dict[str, object]]] | None = None,
) -> bool:
    """Prove that the exact glyphs removed by a row box equal its raw cells."""

    raw_text = "\n".join(str(cell) for cell in row if cell is not None)
    raw_characters = _non_whitespace_character_counts(raw_text)
    observed_characters: Counter[str] = Counter()
    if cell_word_row is not None:
        observed_text = "".join(
            str(word.get("text", ""))
            for cell_words in cell_word_row
            for word in cell_words
        )
        observed_characters = _non_whitespace_character_counts(observed_text)
        if observed_characters and observed_characters != raw_characters:
            return False
    try:
        page_characters = list(getattr(page, "chars", ()) or ())
    except Exception:
        page_characters = []
    removed_characters = _non_whitespace_character_counts(
        "".join(
            str(character.get("text", ""))
            for character in page_characters
            if _layout_object_center_inside_any_box(character, (bbox,))
        )
    )
    if removed_characters:
        return removed_characters == raw_characters
    try:
        bbox_text = page.crop(bbox).extract_text(x_tolerance=1, y_tolerance=3) or ""
    except Exception:
        return False
    bbox_characters = _non_whitespace_character_counts(bbox_text)
    return (
        bool(bbox_characters)
        and bbox_characters == raw_characters
        and (not observed_characters or observed_characters == raw_characters)
    )


def _non_whitespace_character_counts(value: str) -> Counter[str]:
    """Count every observed non-whitespace character with multiplicity."""

    return Counter(character for character in value if not character.isspace())


def _extract_text_without_proven_table_bboxes(
    page: object,
    coordinate_words: list[dict[str, object]],
    bboxes: tuple[tuple[float, float, float, float], ...],
    *,
    coordinate_warnings: list[str] | None = None,
    coordinate_error: str | None = None,
) -> str | None:
    """Extract comparison prose after removing only fully covered table bboxes."""

    try:
        prose_page = page.filter(
            lambda obj: not _layout_object_center_inside_any_box(obj, bboxes)
        )
        text = prose_page.extract_text(x_tolerance=1, y_tolerance=3) or ""
    except Exception:
        return None
    prose_words = [
        word
        for word in coordinate_words
        if not _layout_object_center_inside_any_box(word, bboxes)
    ]
    column_major_text = _high_confidence_column_major_text(
        prose_page,
        prose_words,
    )
    if column_major_text is not None:
        text = column_major_text
    else:
        text = _repair_unique_section_anchor_reading_order(text, prose_words)
    return _repair_body_visual_subscript_order_with_duplicate_fallback(
        text,
        prose_page,
        prose_words,  # type: ignore[arg-type] -- validated coordinate word shape is preserved by bbox filtering.
        coordinate_warnings or [],
        coordinate_error,
    )


def _table_bbox(table: object) -> tuple[float, float, float, float] | None:
    """Return a normalized table bbox, or None when pdfplumber did not provide one."""

    bbox = tuple(float(value) for value in getattr(table, "bbox", ()) or ())  # pdfplumber 的 bbox 是表格定位的主证据。
    return bbox if len(bbox) == 4 else None  # 无四元组坐标时不能安全裁剪或按表题过滤。


def _table_page_bbox(page: object) -> tuple[float, float, float, float] | None:
    """Return the full source-page bounds needed for proportional edge checks."""

    raw_bbox = getattr(page, "bbox", None)
    if not raw_bbox:
        raw_bbox = (
            0.0,
            0.0,
            float(getattr(page, "width", 0) or 0),
            float(getattr(page, "height", 0) or 0),
        )
    try:
        page_bbox = tuple(float(value) for value in raw_bbox)
    except (TypeError, ValueError):
        return None
    if (
        len(page_bbox) != 4
        or page_bbox[2] <= page_bbox[0]
        or page_bbox[3] <= page_bbox[1]
    ):
        return None
    return page_bbox


def _should_skip_detected_table(title: str, table_lines: list[str]) -> bool:
    """Return True when a pdfplumber table object is actually a figure/noise region."""

    cleaned_title = normalize_line(title)  # 统一空白后判断标题类型，避免行号残留影响规则。
    if table_lines and _table_lines_are_visual_only(table_lines):
        return True  # 即使没有 bbox，纯坐标轴/公式碎片也不能进入正文 diff 或表格截图区。
    if _looks_like_figure_caption(cleaned_title):
        return True  # 用户明确不需要图片/图形对比，Figure 误检必须整块跳过。
    if _looks_like_non_table_caption(cleaned_title) and not table_lines:
        return True  # 页眉、单字母坐标轴等空候选没有表格证据，直接丢弃。
    if _table_lines_are_single_column_note_box(table_lines, title=cleaned_title) and not _looks_like_table_caption(cleaned_title):
        return True  # 无表题的一列 Note/说明框不是结构化表格，避免把图片/文本框当表格对比。
    if not table_lines and not _looks_like_table_caption(cleaned_title) and not _looks_like_table_context_caption(cleaned_title):
        return True  # 没有结构化行也没有表格语义时，通常是 OpenCV/pdfplumber 误检。
    return False  # 其余候选保守保留，确保真实表格截图不会被误删。


def _table_lines_are_single_column_note_box(table_lines: list[str], *, title: str = "") -> bool:
    """Return True for one-column note/text boxes misdetected as tables."""

    payloads = [_single_value_table_payload(line) for line in table_lines]  # 提取 Value= 后的可读文本。
    if not payloads or any(payload is None for payload in payloads):
        return False  # 只处理每行最多一个有内容单元格的候选，避免误删多列表格。
    joined = " ".join(payload for payload in payloads if payload)  # 汇总整块说明文本，判断是否是 Note 框。
    content_starts_note = bool(re.search(r"(?i)^\s*(?:note|notes)\s*[:.]", joined))
    title_is_note = bool(re.fullmatch(r"(?i)notes?\s*[:.]?", normalize_line(title)))
    return content_starts_note or title_is_note  # Note 可在首个单元格，也可能只出现在边框上方标题。


def _single_value_table_payload(line: str) -> str | None:
    """Return the payload when one table row has at most one populated cell."""

    text = normalize_line(line)  # 使用抽取层统一空白规则，兼容 pdfplumber 的单元格换行。
    if text.startswith(_TABLE_ROW_PREFIX):
        text = text[len(_TABLE_ROW_PREFIX) :].strip()  # 去掉内部“表格行:”前缀。
    cells = split_table_cells(text)
    if cells and re.fullmatch(r"T\d+", cells[0], flags=re.I):
        cells = cells[1:]  # 去掉物理表编号，避免 T1/T2 影响判断。
    cells = [cell for cell in cells if cell]
    if not cells or len(cells) > 3:
        return None
    payloads: list[str] = []
    for cell in cells:
        field = split_table_field(cell)
        payload = field[1].strip() if field else decode_table_cell(cell)
        if payload:
            payloads.append(normalize_line(payload))
    if len(payloads) > 1:
        return None
    return payloads[0] if payloads else ""  # 全空行也可属于被拆开的 Note 边框，但不能单独触发 Note 判断。


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
    content_fully_represented: bool = False,
    row_alignment_reliable: bool = False,
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
            page_bbox=_table_page_bbox(page),
            content_fully_represented=content_fully_represented,
            row_alignment_reliable=row_alignment_reliable,
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
    candidate_windows: list[list[str]] = []
    for window_left, window_right in (
        (left, right),
        (page_left, page_right),
    ):
        if candidate_windows and window_left == left and window_right == right:
            continue  # 局部窗口已经覆盖整页宽度时不重复抽取。
        try:
            caption_page = page.crop((window_left, top, window_right, bbox[1]))
            caption_text = caption_page.extract_text(x_tolerance=1, y_tolerance=3) or ""
        except Exception:
            continue
        candidates = [
            _strip_caption_line_noise(line)
            for raw_line in caption_text.splitlines()
            if (line := normalize_line(raw_line))
        ]  # 表题候选先去掉页边行号和尾部孤立行号。
        candidate_windows.append(
            [
                candidate
                for candidate in candidates
                if candidate and not _looks_like_non_table_caption(candidate)
            ]
        )

    # 每个窗口先按距 bbox 的近远顺序找明确 Table/Figure 题名。局部窗口中的
    # Figure 不能被整页窗口里更远的上一张 Table 题名覆盖；图中窄线框若在
    # 局部窗口漏掉 ``Figure`` 前缀，第二个整页窗口仍能补回完整题名。
    for candidates in candidate_windows:
        for predicate in (_looks_like_table_caption, _looks_like_figure_caption):
            for candidate in reversed(candidates):
                if predicate(candidate):
                    return candidate
    for candidates in candidate_windows:
        for candidate in reversed(candidates):
            if _looks_like_table_context_caption(candidate):
                return candidate
    return ""


def _strip_caption_line_noise(value: str) -> str:
    """Remove margin line numbers that cling to table/figure captions."""

    cleaned = normalize_line(value)  # 表题清洗先做统一空白，后续正则更稳定。
    cleaned = re.sub(r"^\d{1,3}\s+(?=(?:table|figure|fig\.|表|图)\b)", "", cleaned, flags=re.I)  # 删除 `1 Table ...` 前缀行号。
    if (
        _TABLE_CAPTION_RE.search(cleaned)
        or _FIGURE_CAPTION_RE.search(cleaned)
    ):
        cleaned = re.sub(r"\s+\d{1,3}$", "", cleaned)  # 删除 `Table ... 1` 这类尾部行号，但保留题名主体。
    return normalize_line(cleaned)


def _looks_like_table_caption(value: str) -> bool:
    """Return True when a caption explicitly names a table."""

    return bool(_TABLE_CAPTION_RE.search(normalize_line(value)))  # 复用统一表题正则，覆盖英文 Table 和中文表。


def _looks_like_table_context_caption(value: str) -> bool:
    """Return True for unnumbered text that clearly introduces a table."""

    candidate = normalize_line(value).casefold()  # 无编号表只能靠上下文词判断。
    return bool(
        re.search(
            r"\b(?:table\s+(?:below|following)|(?:the\s+)?following\s+table)\b"
            r"|\bin\s+the\s+table\s+below\b"
            r"|\b(?:revision|change|version)\s+(?:history|record|log)\b"
            r"|修订(?:历史|记录)|变更(?:历史|记录)|下表",
            candidate,
        )
    )


def _looks_like_non_table_caption(value: str) -> bool:
    """Return True for page furniture or tiny axis labels that should not title a table."""

    candidate = normalize_line(value)  # 统一空白，避免页眉里的换行影响判断。
    if not candidate:
        return True
    if candidate.isdigit():
        return True
    if re.fullmatch(r"[A-Za-z]", candidate) and candidate.upper() in _WATERMARK_TEXT_CHARS | frozenset({"X", "Y"}):
        return True  # 单个 D/R/A/F/T 或 X/Y 更可能是水印/坐标轴，不是表题。
    if looks_like_page_bearing_running_header(candidate):
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
    geometry_count = line_count + rect_count
    if (
        geometry_count >= 6
        and _looks_like_table_context_caption(text)
        and all(
            re.search(rf"(?i)\b{header}\b", text)
            for header in ("revision", "date", "description")
        )
    ):
        return True  # 修订记录常只有 6--9 个矩形；表引导语、完整三列表头和几何必须同时成立。
    return geometry_count >= 10


def _table_lines_from_rows(
    rows: list[list[object]],
    table_number: int,
    *,
    cell_word_rows: list[list[list[dict[str, object]]]] | None = None,
) -> list[str]:
    """Convert raw pdfplumber table rows into stable comparison lines."""

    lines, _fully_represented = _table_lines_from_rows_with_coverage(
        rows,
        table_number,
        cell_word_rows=cell_word_rows,
    )
    return lines


def _table_lines_from_rows_with_coverage(
    rows: list[list[object]],
    table_number: int,
    *,
    cell_word_rows: list[list[list[dict[str, object]]]] | None = None,
) -> tuple[list[str], bool]:
    """Format rows and report whether every source data cell remains represented."""

    lines, content_lossless, alignment_reliable = _table_lines_from_rows_with_evidence(
        rows,
        table_number,
        cell_word_rows=cell_word_rows,
    )
    return lines, content_lossless and alignment_reliable


def _table_lines_from_rows_with_evidence(
    rows: list[list[object]],
    table_number: int,
    *,
    cell_word_rows: list[list[list[dict[str, object]]]] | None = None,
) -> tuple[list[str], bool, bool]:
    """Return rows plus independent content-loss and row-alignment evidence."""

    rows = _rejoin_table_cell_subscripts(rows, cell_word_rows)
    raw_rows: list[list[list[str]]] = []
    raw_word_rows: list[list[list[dict[str, object]]] | None] = []
    for row_index, row in enumerate(rows):
        raw_row = [_table_cell_lines(cell) for cell in row]
        if not any(any(cell_lines) for cell_lines in raw_row):
            continue
        raw_rows.append(raw_row)
        raw_word_rows.append(
            cell_word_rows[row_index]
            if cell_word_rows is not None and row_index < len(cell_word_rows)
            else None
        )
    # 文字行与单元格几何必须同索引过滤；后面只允许几何证明过的多字符纯字母 Symbol 参与拆行。
    if not raw_rows:  # 空表格没有可比较内容。
        return [], False, False

    header_index = _find_table_header_row(raw_rows)  # 在表格前几行扫描表头，处理标题行/空白行插在表头前的情况。
    if header_index is None:
        header = _default_header_for_table(raw_rows)  # 续页缺失表头时，用常见协议表宽恢复列含义。
        data_rows = raw_rows  # 没有真实表头时，所有行都作为数据行输出。
        data_word_rows = raw_word_rows
    else:
        header = _clean_table_header(raw_rows[header_index])  # 清洗扫描到的表头，用于 Header=Value 片段。
        data_rows = raw_rows[header_index + 1 :]  # 有真实表头时跳过表头本身，只输出数据行。
        data_word_rows = raw_word_rows[header_index + 1 :]
    content_lossless = header_index in {None, 0}  # 表头前的非空标题/注释行会被跳过，不能授权删除 bbox 原文。
    alignment_reliable = True
    lines: list[str] = []  # 输出给比较器的结构化表格行。
    for row, cell_word_row in zip(data_rows, data_word_rows, strict=True):
        expanded_rows, expansion_alignment_reliable = _expand_table_row_with_evidence(
            row,
            header,
            cell_word_row=cell_word_row,
        )
        row_content_lossless = _expanded_rows_preserve_source_cells(row, expanded_rows)
        content_lossless = content_lossless and row_content_lossless
        alignment_reliable = (
            alignment_reliable
            and row_content_lossless
            and expansion_alignment_reliable
            and _expanded_rows_preserve_source_alignment(
                row,
                expanded_rows,
                cell_word_row=cell_word_row,
            )
        )
        for expanded_row in expanded_rows:  # 一个物理表格行可能包含多条参数记录。
            line = _format_table_row(expanded_row, header, table_number)
            if line:
                lines.append(line)
    has_lines = bool(lines)
    return lines, content_lossless and has_lines, alignment_reliable and has_lines


def _expanded_rows_preserve_source_cells(
    source_row: list[list[str]],
    expanded_rows: list[list[str]],
) -> bool:
    """Verify each physical cell's characters survive within the same column."""

    if not expanded_rows:
        return False
    for column_index, source_lines in enumerate(source_row):
        source_characters = _non_whitespace_character_counts("\n".join(source_lines))
        expanded_characters = _non_whitespace_character_counts(
            "\n".join(
                expanded_row[column_index]
                for expanded_row in expanded_rows
                if column_index < len(expanded_row)
            )
        )
        if source_characters - expanded_characters:
            return False
    return True


def _expanded_rows_preserve_source_alignment(
    source_row: list[list[str]],
    expanded_rows: list[list[str]],
    *,
    cell_word_row: list[list[dict[str, object]]] | None = None,
) -> bool:
    """Reject bbox replacement when text or geometry cannot prove row pairing."""

    if len(expanded_rows) != 1:
        return _expanded_rows_match_observed_baselines(
            source_row,
            expanded_rows,
            cell_word_row,
        )
    multiline_columns = sum(
        len([line for line in source_lines if normalize_line(line)]) > 1
        for source_lines in source_row
    )
    return multiline_columns < 2


def _expanded_rows_match_observed_baselines(
    source_row: list[list[str]],
    expanded_rows: list[list[str]],
    cell_word_row: list[list[dict[str, object]]] | None,
) -> bool:
    """Use available cell-word geometry as a veto on cross-column mispairing."""

    if cell_word_row is None or len(cell_word_row) != len(source_row):
        return True  # 无坐标时保留既有的文本证据路径；有坐标则不得忽略冲突。
    target_count = len(expanded_rows)
    directly_mapped: list[list[tuple[float, float]]] = []
    for column_index, source_lines in enumerate(source_row):
        if len(source_lines) != target_count:
            continue
        expanded_values = [
            expanded_row[column_index]
            for expanded_row in expanded_rows
            if column_index < len(expanded_row)
        ]
        if len(expanded_values) != target_count or any(
            _non_whitespace_character_counts(source_line)
            != _non_whitespace_character_counts(expanded_value)
            for source_line, expanded_value in zip(
                source_lines,
                expanded_values,
                strict=True,
            )
        ):
            continue
        observations = _geometry_line_observations(
            source_lines,
            cell_word_row[column_index],
        )
        if observations is not None:
            directly_mapped.append(observations)
    if len(directly_mapped) < 2:
        return True  # 单列坐标不能证明跨列行归属，也不能凭证据缺失伪造冲突。
    for row_index in range(target_count):
        centers = [observations[row_index][0] for observations in directly_mapped]
        heights = [observations[row_index][1] for observations in directly_mapped]
        tolerance = max(1.5, min(heights) * 0.45)
        if max(centers) - min(centers) > tolerance:
            return False
    return True


def _rejoin_table_cell_subscripts(
    rows: list[list[object]],
    cell_word_rows: list[list[list[dict[str, object]]]] | None,
) -> list[list[object]]:
    """Rejoin only script fragments proven by size, placement, and schema."""

    if cell_word_rows is None or len(cell_word_rows) != len(rows):
        return rows
    header_index, header = _table_header_context(rows)
    compound_script_columns = {
        index for index, label in enumerate(header) if _is_symbol_header(label)
    }
    repaired_rows: list[list[object]] = []
    for row_index, (row, word_cells) in enumerate(
        zip(rows, cell_word_rows, strict=False)
    ):
        if len(word_cells) != len(row):
            return rows
        repaired_rows.append(
            [
                _rejoin_table_cell_subscript_lines(
                    cell,
                    words,
                    allow_compound=(
                        (header_index is None or row_index > header_index)
                        and column_index in compound_script_columns
                    ),
                )
                for column_index, (cell, words) in enumerate(
                    zip(row, word_cells, strict=False)
                )
            ]
        )
    return _rejoin_geometry_aligned_group_labels(
        repaired_rows,
        cell_word_rows,
        header_index=header_index,
        header=header,
    )


def _table_header_context(
    rows: list[list[object]],
) -> tuple[int | None, list[str]]:
    """Return the observed header index and normalized labels without dropping rows."""

    raw_rows = [[_table_cell_lines(cell) for cell in row] for row in rows]
    header_index = _find_table_header_row(raw_rows)
    if header_index is None:
        return None, _default_header_for_table(raw_rows)
    return header_index, _clean_table_header(raw_rows[header_index])


def _rejoin_table_cell_subscript_lines(
    cell: object,
    words: list[dict[str, object]],
    *,
    allow_compound: bool = False,
) -> object:
    """Return a cell with adjacent visual subscript lines joined losslessly."""

    lines = _table_cell_lines(cell)
    if allow_compound:
        compound_lines = _geometry_compound_script_lines(lines, words)
        if compound_lines is not None:
            return "\n".join(compound_lines)
    if len(lines) < 2 or len(words) < 2:
        return cell
    inline_repair = _rebuild_table_inline_subscript_lines(lines, words)
    if inline_repair is not None:
        return inline_repair
    line_evidence = _visual_subscript_line_evidence(words)
    if len(line_evidence) != len(lines) or any(
        _non_whitespace_character_counts(line)
        != _non_whitespace_character_counts(observed_text)
        for line, (observed_text, _merge_with_next) in zip(
            lines,
            line_evidence,
            strict=False,
        )
    ):
        return cell
    if not any(merge_with_next for _text, merge_with_next in line_evidence):
        return cell
    repaired: list[str] = []
    index = 0
    while index < len(lines):
        if (
            index + 1 < len(lines)
            and line_evidence[index][1]
        ):
            repaired.append(lines[index] + lines[index + 1])
            index += 2
            continue
        repaired.append(lines[index])
        index += 1
    return "\n".join(repaired)


def _rebuild_table_inline_subscript_lines(
    raw_lines: list[str],
    words: list[dict[str, object]],
) -> str | None:
    """Place a lowered suffix after its in-line base using lossless geometry.

    pdfplumber may serialize ``f_b /2 GHz`` as ``f /2 GHz\nb``.  The older
    repair only inspected the last word (``GHz``), so it could not discover
    the real base in the middle of the preceding line.  This helper accepts a
    merge only when every word on the lowered visual line has one unique base,
    each source line is character-for-character conserved, and the final cell
    contains exactly the original non-whitespace character multiset.
    """

    observed_words: list[dict[str, object]] = []
    for word in words:
        try:
            text = normalize_line(str(word.get("text", "")))
            x0 = float(word["x0"])
            x1 = float(word["x1"])
            top = float(word["top"])
            bottom = float(word["bottom"])
        except (KeyError, TypeError, ValueError):
            return None
        if not text or x1 <= x0 or bottom <= top:
            return None
        observed_words.append(
            {
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": bottom,
            }
        )

    indexed_lines = _indexed_visual_word_lines(observed_words)
    if len(indexed_lines) != len(raw_lines):
        return None
    observed_line_texts = [
        _words_to_visual_line([observed_words[index] for index in line])
        for line in indexed_lines
    ]
    if any(
        _non_whitespace_character_counts(raw_line)
        != _non_whitespace_character_counts(observed_line)
        for raw_line, observed_line in zip(
            raw_lines,
            observed_line_texts,
            strict=False,
        )
    ):
        return None

    for line_index in range(len(indexed_lines) - 1):
        base_indexes = indexed_lines[line_index]
        suffix_indexes = indexed_lines[line_index + 1]
        candidate_edges = [
            (base_index, suffix_index)
            for base_index in base_indexes
            for suffix_index in suffix_indexes
            if _words_form_visual_subscript(
                observed_words[base_index],
                observed_words[suffix_index],
            )
        ]
        endpoint_degrees = Counter(
            index
            for edge in candidate_edges
            for index in edge
        )
        accepted_edges = [
            edge
            for edge in candidate_edges
            if endpoint_degrees[edge[0]] == endpoint_degrees[edge[1]] == 1
        ]
        if len(suffix_indexes) != 1 or len(accepted_edges) != 1:
            continue  # 多行参数组仍交给既有的行扩展逻辑，不能压成一个复合单元格。
        base_index, suffix_index = accepted_edges[0]
        if (
            str(observed_words[base_index]["text"]) != "f"
            or str(observed_words[suffix_index]["text"]) != "b"
        ):
            continue  # 当前新增路径只补恢复频率基准 f_b；其它下标继续走既有通用证据门禁。
        base_position = base_indexes.index(base_index)
        if base_position > len(base_indexes) - 3:
            continue  # 只处理基符后仍有运算符和单位的行内下标；行尾下标已有旧逻辑覆盖。
        following_text = " ".join(
            str(observed_words[index]["text"])
            for index in base_indexes[base_position + 1 :]
        )
        if re.match(r"^[⁄/]\s*2(?:\s|$)", following_text) is None:
            continue  # 只在源行明确呈现 f /2 ...、降低行呈现 b 时调整字符次序。
        if {suffix for _base, suffix in accepted_edges} != set(suffix_indexes):
            continue  # 降低行含普通文字或一对多歧义时保留原始换行。
        combined_indexes = sorted(
            [*base_indexes, *suffix_indexes],
            key=lambda index: (
                float(observed_words[index]["x0"]),
                float(observed_words[index]["x1"]),
                float(observed_words[index]["top"]),
            ),
        )
        rebuilt_line = _rebuild_body_subscript_visual_line(
            combined_indexes,
            observed_words,
            accepted_edges,
        )
        repaired_lines = [
            *observed_line_texts[:line_index],
            rebuilt_line,
            *observed_line_texts[line_index + 2 :],
        ]
        repaired = "\n".join(repaired_lines)
        if (
            _non_whitespace_character_counts(repaired)
            == _non_whitespace_character_counts("\n".join(raw_lines))
        ):
            return repaired
    return None


def _geometry_compound_script_lines(
    raw_lines: list[str],
    words: list[dict[str, object]],
) -> list[str] | None:
    """Rebuild base/subscript/qualifier records from lossless Symbol-cell geometry."""

    if len(raw_lines) < 2 or len(words) < 3:
        return None
    observed_words = [
        word
        for word in words
        if normalize_line(str(word.get("text", "")))
        and _word_effective_size(word) > 0
    ]
    if len(observed_words) < 3:
        return None
    observed_words = _coalesce_fragmented_symbol_qualifiers(observed_words)
    raw_characters = _non_whitespace_character_counts("\n".join(raw_lines))
    observed_characters = _non_whitespace_character_counts(
        "".join(str(word.get("text", "")) for word in observed_words)
    )
    if not raw_characters or raw_characters != observed_characters:
        return None  # 任何漏词、额外水印词或重复观测都撤销几何改写权。

    components = _script_word_components(observed_words)
    if components is None:
        return None
    rebuilt: list[tuple[float, float, str]] = []
    saw_compound_script = False
    for component in components:
        ordered = sorted(component, key=lambda word: float(word["x0"]))
        subscript_indexes = [
            index
            for index in range(len(ordered) - 1)
            if _words_form_visual_subscript(ordered[index], ordered[index + 1])
        ]
        if len(subscript_indexes) > 1:
            return None
        if subscript_indexes:
            subscript_index = subscript_indexes[0]
            if subscript_index != 0 or len(ordered) not in {2, 3}:
                return None
            if len(ordered) == 3:
                if not _words_form_visual_qualifier(
                    ordered[0],
                    ordered[1],
                    ordered[2],
                ):
                    return None
                saw_compound_script = True
        elif len(ordered) == 2:
            if not _words_form_visual_qualifier(
                ordered[0],
                ordered[0],
                ordered[1],
            ):
                return None
        elif len(ordered) != 1:
            return None

        text = "".join(normalize_line(str(word.get("text", ""))) for word in ordered)
        if not text:
            return None
        centers = [
            (float(word["top"]) + float(word["bottom"])) / 2
            for word in ordered
        ]
        rebuilt.append((median(centers), float(ordered[0]["x0"]), text))

    if not saw_compound_script:
        return None  # 简单双行下标继续走原 occurrence-bound 路径，缩小新规则作用面。
    lines = [text for _center, _x0, text in sorted(rebuilt)]
    if _non_whitespace_character_counts("\n".join(lines)) != raw_characters:
        return None
    if not _rebuilt_script_lines_match_raw_occurrences(raw_lines, lines):
        return None  # 全单元格必须能唯一映射回连续的原始物理行，孤立括号或错序 token 直接撤销改写。
    return lines


def _coalesce_fragmented_symbol_qualifiers(
    words: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Join only contiguous same-baseline pieces of ``(x)`` or ``base(x)``."""

    visual_lines: list[tuple[float, list[dict[str, object]]]] = []
    for word in sorted(words, key=lambda item: float(item["top"])):
        top = float(word["top"])
        if visual_lines and abs(top - visual_lines[-1][0]) <= 0.8:
            visual_lines[-1][1].append(word)
        else:
            visual_lines.append((top, [word]))
    ordered = [
        word
        for _top, line_words in visual_lines
        for word in sorted(line_words, key=lambda item: float(item["x0"]))
    ]
    rebuilt: list[dict[str, object]] = []
    index = 0
    while index < len(ordered):
        chosen: list[dict[str, object]] | None = None
        for end in range(min(len(ordered), index + 4), index + 1, -1):
            candidate = ordered[index:end]
            if len(candidate) < 2 or not _words_share_compact_baseline(candidate):
                continue
            joined = "".join(
                normalize_line(str(word.get("text", ""))) for word in candidate
            )
            if re.fullmatch(r"\([A-Za-z0-9+-]{1,4}\)", joined) or re.fullmatch(
                r"(?:[^\W\d_]|[\ue000-\uf8ff])[\w_.+-]*\([A-Za-z0-9+-]{1,4}\)",
                joined,
            ):
                chosen = candidate
                break
        if chosen is None:
            rebuilt.append(ordered[index])
            index += 1
            continue
        merged = dict(chosen[0])
        merged.update(
            {
                "text": "".join(
                    normalize_line(str(word.get("text", ""))) for word in chosen
                ),
                "x0": min(float(word["x0"]) for word in chosen),
                "x1": max(float(word["x1"]) for word in chosen),
                "top": min(float(word["top"]) for word in chosen),
                "bottom": max(float(word["bottom"]) for word in chosen),
                "size": max(_word_effective_size(word) for word in chosen),
            }
        )
        rebuilt.append(merged)
        index += len(chosen)
    return rebuilt


def _words_share_compact_baseline(words: list[dict[str, object]]) -> bool:
    """Require qualifier fragments to share one baseline and touch horizontally."""

    try:
        tops = [float(word["top"]) for word in words]
        heights = [float(word["bottom"]) - float(word["top"]) for word in words]
        ordered = sorted(words, key=lambda word: float(word["x0"]))
        gaps = [
            float(right["x0"]) - float(left["x1"])
            for left, right in zip(ordered, ordered[1:], strict=False)
        ]
    except (KeyError, TypeError, ValueError):
        return False
    if not heights or any(height <= 0 for height in heights):
        return False
    scale = min(heights)
    return (
        max(tops) - min(tops) <= max(0.8, scale * 0.14)
        and all(-scale * 0.20 <= gap <= max(1.0, scale * 0.25) for gap in gaps)
    )


def _rebuilt_script_lines_match_raw_occurrences(
    raw_lines: list[str],
    rebuilt_lines: list[str],
) -> bool:
    """Prove one unique contiguous raw-line partition for rebuilt symbol records."""

    raw = [line for line in raw_lines if normalize_line(line)]
    rebuilt = [line for line in rebuilt_lines if normalize_line(line)]
    if not raw or not rebuilt:
        return False

    memo: dict[tuple[int, int], int] = {}

    def count_partitions(raw_index: int, rebuilt_index: int) -> int:
        key = (raw_index, rebuilt_index)
        if key in memo:
            return memo[key]
        if rebuilt_index == len(rebuilt):
            return 1 if raw_index == len(raw) else 0
        if raw_index >= len(raw):
            return 0
        target = _non_whitespace_character_counts(rebuilt[rebuilt_index])
        accumulated: Counter[str] = Counter()
        total = 0
        for end in range(raw_index, len(raw)):
            accumulated.update(_non_whitespace_character_counts(raw[end]))
            if accumulated - target:
                break
            if accumulated == target:
                total += count_partitions(end + 1, rebuilt_index + 1)
                if total > 1:
                    memo[key] = 2
                    return 2
        memo[key] = total
        return total

    return count_partitions(0, 0) == 1


def _script_word_components(
    words: list[dict[str, object]],
) -> list[list[dict[str, object]]] | None:
    """Build occurrence-bound script groups from unique base-to-suffix evidence."""

    if not _script_geometry_is_one_to_one(words):
        return None
    ordered_indexes = sorted(
        range(len(words)),
        key=lambda index: (
            (float(words[index]["top"]) + float(words[index]["bottom"])) / 2,
            float(words[index]["x0"]),
        ),
    )
    claimed: set[int] = set()
    components: list[list[dict[str, object]]] = []
    for base_index in ordered_indexes:
        if base_index in claimed:
            continue
        suffix_candidates = [
            suffix_index
            for suffix_index in ordered_indexes
            if suffix_index not in claimed
            and suffix_index != base_index
            and _words_form_visual_subscript(words[base_index], words[suffix_index])
        ]
        if len(suffix_candidates) > 1:
            return None
        if not suffix_candidates:
            continue
        suffix_index = suffix_candidates[0]
        qualifier_candidates = [
            qualifier_index
            for qualifier_index in ordered_indexes
            if qualifier_index not in claimed
            and qualifier_index not in {base_index, suffix_index}
            and _words_form_visual_qualifier(
                words[base_index],
                words[suffix_index],
                words[qualifier_index],
            )
        ]
        if len(qualifier_candidates) > 1:
            return None
        component_indexes = [base_index, suffix_index, *qualifier_candidates]
        claimed.update(component_indexes)
        components.append([words[index] for index in component_indexes])

    for base_index in ordered_indexes:
        if base_index in claimed:
            continue
        qualifier_candidates = [
            qualifier_index
            for qualifier_index in ordered_indexes
            if qualifier_index not in claimed
            and qualifier_index != base_index
            and _words_form_visual_qualifier(
                words[base_index],
                words[base_index],
                words[qualifier_index],
            )
        ]
        if len(qualifier_candidates) > 1:
            return None
        if qualifier_candidates:
            qualifier_index = qualifier_candidates[0]
            claimed.update({base_index, qualifier_index})
            components.append([words[base_index], words[qualifier_index]])

    components.extend(
        [[words[index]] for index in ordered_indexes if index not in claimed]
    )
    return components


def _script_geometry_is_one_to_one(words: list[dict[str, object]]) -> bool:
    """Reject any suffix or qualifier that is geometrically valid for two bases."""

    indexes = range(len(words))
    subscript_edges = [
        (base_index, suffix_index)
        for base_index in indexes
        for suffix_index in indexes
        if base_index != suffix_index
        and _words_form_visual_subscript(words[base_index], words[suffix_index])
    ]
    base_degrees = Counter(base_index for base_index, _suffix_index in subscript_edges)
    suffix_degrees = Counter(suffix_index for _base_index, suffix_index in subscript_edges)
    if any(degree > 1 for degree in (*base_degrees.values(), *suffix_degrees.values())):
        return False

    suffix_for_base = dict(subscript_edges)
    suffix_indexes = set(suffix_for_base.values())
    qualifier_edges: list[tuple[int, int]] = []
    for base_index in indexes:
        if base_index in suffix_indexes:
            continue
        preceding_index = suffix_for_base.get(base_index, base_index)
        for qualifier_index in indexes:
            if qualifier_index in {base_index, preceding_index}:
                continue
            if _words_form_visual_qualifier(
                words[base_index],
                words[preceding_index],
                words[qualifier_index],
            ):
                qualifier_edges.append((base_index, qualifier_index))
    anchor_degrees = Counter(base_index for base_index, _qualifier_index in qualifier_edges)
    qualifier_degrees = Counter(qualifier_index for _base_index, qualifier_index in qualifier_edges)
    return not any(
        degree > 1
        for degree in (*anchor_degrees.values(), *qualifier_degrees.values())
    )


def _words_form_visual_qualifier(
    base_word: dict[str, object],
    preceding_word: dict[str, object],
    qualifier_word: dict[str, object],
) -> bool:
    """Require a unique compact parenthesized qualifier beside one symbol."""

    qualifier_text = normalize_line(str(qualifier_word.get("text", "")))
    if not re.fullmatch(r"\([A-Za-z0-9+-]{1,4}\)", qualifier_text):
        return False
    base_text = normalize_line(str(base_word.get("text", "")))
    if not re.fullmatch(r"(?:[^\W\d_]|[\ue000-\uf8ff])[\w_.+-]*", base_text):
        return False
    try:
        base_size = _word_effective_size(base_word)
        qualifier_size = _word_effective_size(qualifier_word)
        base_top = float(base_word["top"])
        base_bottom = float(base_word["bottom"])
        qualifier_top = float(qualifier_word["top"])
        qualifier_bottom = float(qualifier_word["bottom"])
        horizontal_gap = float(qualifier_word["x0"]) - float(preceding_word["x1"])
    except (KeyError, TypeError, ValueError):
        return False
    if base_size <= 0 or qualifier_size <= 0:
        return False
    return (
        0.55 <= qualifier_size / base_size <= 1.05
        and -base_size * 0.40 <= qualifier_top - base_top <= base_size * 0.15
        and min(base_bottom, qualifier_bottom) > max(base_top, qualifier_top)
        and -base_size * 0.20 <= horizontal_gap <= base_size * 0.35
    )


def _rejoin_geometry_aligned_group_labels(
    rows: list[list[object]],
    cell_word_rows: list[list[list[dict[str, object]]]],
    *,
    header_index: int | None,
    header: list[str],
) -> list[list[object]]:
    """Attach a leading group label only when two columns prove N aligned records."""

    if not header:
        return rows
    repaired_rows = [list(row) for row in rows]
    data_start = 0 if header_index is None else header_index + 1
    for row_index in range(data_start, len(repaired_rows)):
        row = repaired_rows[row_index]
        if row_index >= len(cell_word_rows) or len(cell_word_rows[row_index]) != len(row):
            continue
        labels = _header_labels_for_row(header, len(row))
        row_lines = [_table_cell_lines(cell) for cell in row]
        column_values = [
            _logical_cell_values(lines, labels[index])
            for index, lines in enumerate(row_lines)
        ]
        target_count = _aligned_non_descriptor_value_count(column_values, labels)
        if target_count <= 1:
            continue
        reference_observations = [
            observations
            for column_index, (lines, label) in enumerate(
                zip(row_lines, labels, strict=False)
            )
            if not _is_descriptor_header(label)
            and len(lines) == target_count
            and (
                observations := _geometry_line_observations(
                    lines,
                    cell_word_rows[row_index][column_index],
                )
            )
            is not None
        ]
        if len(reference_observations) < 2:
            continue
        for column_index, (lines, label) in enumerate(
            zip(row_lines, labels, strict=False)
        ):
            if not _is_descriptor_header(label) or len(lines) != target_count + 1:
                continue
            descriptor_observations = _geometry_line_observations(
                lines,
                cell_word_rows[row_index][column_index],
            )
            if descriptor_observations is None:
                continue
            if not all(
                _group_label_is_unaligned_then_rows_match(
                    descriptor_observations,
                    reference,
                )
                for reference in reference_observations
            ):
                continue
            merged_lines = [f"{lines[0]} — {lines[1]}", *lines[2:]]
            row[column_index] = "\n".join(merged_lines)
    return repaired_rows


def _geometry_line_observations(
    raw_lines: list[str],
    words: list[dict[str, object]],
) -> list[tuple[float, float]] | None:
    """Return (center, height) only when visual lines exactly match raw lines."""

    grouped: list[tuple[float, list[dict[str, object]]]] = []
    for word in sorted(
        words,
        key=lambda item: (
            float(item.get("top", 0) or 0),
            float(item.get("x0", 0) or 0),
        ),
    ):
        try:
            top = float(word["top"])
        except (KeyError, TypeError, ValueError):
            return None
        if grouped and abs(top - grouped[-1][0]) <= 1.5:
            grouped[-1][1].append(word)
        else:
            grouped.append((top, [word]))
    visual_lines = [normalize_line(_words_to_visual_line(line_words)) for _top, line_words in grouped]
    if [normalize_line(line) for line in raw_lines] != visual_lines:
        return None
    observations: list[tuple[float, float]] = []
    for _top, line_words in grouped:
        upper = min(float(word["top"]) for word in line_words)
        lower = max(float(word["bottom"]) for word in line_words)
        observations.append(((upper + lower) / 2, lower - upper))
    return observations


def _group_label_is_unaligned_then_rows_match(
    descriptor: list[tuple[float, float]],
    reference: list[tuple[float, float]],
) -> bool:
    """Prove one extra descriptor line precedes N aligned value rows."""

    if len(descriptor) != len(reference) + 1 or not reference:
        return False
    tolerances = [
        max(1.5, min(descriptor[index + 1][1], reference[index][1]) * 0.45)
        for index in range(len(reference))
    ]
    if any(
        abs(descriptor[index + 1][0] - reference[index][0]) > tolerances[index]
        for index in range(len(reference))
    ):
        return False
    first_tolerance = max(1.5, min(descriptor[0][1], reference[0][1]) * 0.45)
    return abs(descriptor[0][0] - reference[0][0]) > first_tolerance


def _visual_subscript_line_evidence(
    words: list[dict[str, object]],
) -> list[tuple[str, bool]]:
    """Return ordered cell lines and occurrence-bound subscript evidence."""

    word_lines: list[tuple[float, list[dict[str, object]]]] = []
    for word in sorted(
        words,
        key=lambda item: (
            float(item.get("top", 0) or 0),
            float(item.get("x0", 0) or 0),
        ),
    ):
        try:
            top = float(word["top"])
        except (KeyError, TypeError, ValueError):
            continue
        if word_lines and abs(top - word_lines[-1][0]) <= 1.5:
            word_lines[-1][1].append(word)
        else:
            word_lines.append((top, [word]))

    ordered_lines = [
        (
            normalize_line(
                " ".join(
                    str(word.get("text", ""))
                    for word in sorted(
                        line_words,
                        key=lambda word: float(word["x0"]),
                    )
                )
            ),
            line_words,
        )
        for _top, line_words in word_lines
    ]
    merge_flags = [False] * len(ordered_lines)
    for index, ((_current_text, current_words), (_next_text, next_words)) in enumerate(
        zip(ordered_lines, ordered_lines[1:], strict=False)
    ):
        if len(next_words) != 1:
            continue
        current_words = sorted(current_words, key=lambda word: float(word["x0"]))
        suffix_word = next_words[0]
        base_word = current_words[-1]
        if not _words_form_visual_subscript(base_word, suffix_word):
            continue
        merge_flags[index] = True
    return [
        (text, merge_flags[index])
        for index, (text, _words) in enumerate(ordered_lines)
    ]


def _words_form_visual_subscript(
    base_word: dict[str, object],
    suffix_word: dict[str, object],
    *,
    require_lowered_bottom: bool = False,
) -> bool:
    """Require smaller, lowered, touching text within the base's vertical span."""

    base_text = normalize_line(str(base_word.get("text", "")))
    suffix_text = normalize_line(str(suffix_word.get("text", "")))
    if not re.fullmatch(r"(?:[^\W\d_]|[\ue000-\uf8ff])[\w_.+-]*", base_text):
        return False
    if not (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,11}", suffix_text)
        or _SIGNED_RATIONAL_SUBSCRIPT_RE.fullmatch(suffix_text)
    ):
        return False
    try:
        base_size = _word_effective_size(base_word)
        suffix_size = _word_effective_size(suffix_word)
        base_top = float(base_word["top"])
        base_bottom = float(base_word["bottom"])
        base_x1 = float(base_word["x1"])
        suffix_top = float(suffix_word["top"])
        suffix_bottom = float(suffix_word["bottom"])
        suffix_x0 = float(suffix_word["x0"])
    except (KeyError, TypeError, ValueError):
        return False
    if base_size <= 0 or suffix_size <= 0:
        return False
    size_ratio = suffix_size / base_size
    vertical_offset = suffix_top - base_top
    bottom_offset = suffix_bottom - base_bottom
    horizontal_gap = suffix_x0 - base_x1
    return (
        0.55 <= size_ratio <= 0.90
        and base_size * 0.15 <= vertical_offset <= base_size * 0.65
        and suffix_top < base_bottom
        and (
            not require_lowered_bottom
            or bottom_offset >= base_size * 0.10
        )
        and -base_size * 0.20 <= horizontal_gap <= base_size * 0.35
    )


def _body_words_form_visual_subscript(
    base_word: dict[str, object],
    suffix_word: dict[str, object],
) -> bool:
    """Extend the same geometry to a symbol at the end of a wrapped token."""

    base_text = normalize_line(str(base_word.get("text", "")))
    suffix_text = normalize_line(str(suffix_word.get("text", "")))
    if (
        _looks_like_body_technical_subscript_pair(base_text, suffix_text)
        and _words_form_visual_subscript(
            base_word,
            suffix_word,
            require_lowered_bottom=True,
        )
    ):
        return True
    trailing_symbol = re.search(
        r"(?:^|[^\w])(?P<symbol>(?:[^\W\d_]|[\ue000-\uf8ff])[\w_.+-]*)$",
        base_text,
        flags=re.UNICODE,
    )
    if trailing_symbol is None:
        return False
    if not _looks_like_body_technical_subscript_pair(
        trailing_symbol.group("symbol"),
        suffix_text,
    ):
        return False
    symbol_proxy = dict(base_word)
    symbol_proxy["text"] = trailing_symbol.group("symbol")
    return _words_form_visual_subscript(
        symbol_proxy,
        suffix_word,
        require_lowered_bottom=True,
    )


def _looks_like_body_technical_subscript_pair(base: str, suffix: str) -> bool:
    """Require an identifier-shaped base and a known subscript-shaped suffix.

    Geometry alone is insufficient in ordinary prose: a lowered enumeration
    letter beside a word such as ``Mode A`` can have the same bounding-box
    relation as a real engineering subscript.  Body repair is therefore
    limited to compact uppercase identifiers (plus the observed ``J4u``
    family) and numeric or known technical suffixes.
    """

    technical_base = bool(
        re.fullmatch(r"[A-Z][A-Z0-9_]{0,7}", base)
        or re.fullmatch(r"[A-Z]\d+[a-z]", base)
    )
    technical_suffix = bool(
        re.fullmatch(r"\d+[A-Za-z0-9_.+-]{0,5}", suffix)
        or re.fullmatch(r"(?:RMS|TX|ISI)\d*", suffix)
    )
    formula_pair = (
        base in {"S", "S0", "t", "Tavg", "f"}
        and suffix in {"i", "J"}
    )  # OIF formula prose uses S_i, t_i, Tavg_i, S0_i, and f_J.
    voltage_threshold_pair = bool(
        base == "V" and _SIGNED_RATIONAL_SUBSCRIPT_RE.fullmatch(suffix)
    )  # OIF threshold prose使用 V_{-1}, V_{-1/3}, V_{1/3}, V_1；仍需外层完整几何与字符守恒。
    known_engineering_pair = is_known_engineering_symbol_letter_suffix(base, suffix)
    return (
        (technical_base and technical_suffix)
        or formula_pair
        or voltage_threshold_pair
        or known_engineering_pair
    )


def _word_effective_size(word: dict[str, object]) -> float:
    """Return observed font size, falling back to the word bbox height."""

    try:
        explicit_size = float(word.get("size") or 0)
    except (TypeError, ValueError):
        explicit_size = 0.0
    if math.isfinite(explicit_size) and explicit_size > 0:
        return explicit_size
    try:
        bbox_height = float(word["bottom"]) - float(word["top"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    return bbox_height if math.isfinite(bbox_height) and bbox_height > 0 else 0.0


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
        value = "\n".join(lines)  # 保留真实 cell 行边界；字面除号/斜杠不能与换行共用编码。
    return value  # 没有字符坐标证明时，重复乘号也可能是公式编辑，必须原样保留。


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
    """Infer only strongly proven parameter continuations; otherwise stay neutral."""

    widths = [len(row) for row in rows if row]  # pdfplumber 续页通常仍保留正确列数。
    if not widths:
        return []
    width = max(set(widths), key=lambda value: (widths.count(value), value))  # 取出现最多的列宽，避免偶发跨列行干扰。
    if width == 4 and _looks_like_headerless_parameter_table(rows):
        return ["Parameter", "Symbol", "Value", "Units"]
    return [f"Column {index + 1}" for index in range(width)]  # 列数不能证明 Parameter/Symbol 等业务语义。


def _looks_like_headerless_parameter_table(rows: list[list[list[str]]]) -> bool:
    """Require independent descriptor, symbol, numeric, and unit evidence on a continuation."""

    non_empty_rows = [row for row in rows if any(any(cell) for cell in row)]
    four_column_rows = [row for row in non_empty_rows if len(row) == 4]
    if len(four_column_rows) < 8 or len(four_column_rows) / len(non_empty_rows) < 0.90:
        return False

    descriptor_hits = 0
    symbol_hits = 0
    value_hits = 0
    unit_hits = 0
    known_unit_hits = 0
    for descriptor, symbol, value, units in four_column_rows:
        descriptor_text = normalize_line(" ".join(descriptor))
        if len(descriptor_text) >= 12 and len(re.findall(r"[A-Za-z]{2,}", descriptor_text)) >= 2:
            descriptor_hits += 1

        symbol_lines = [normalize_line(line) for line in symbol if normalize_line(line)]
        symbol_text = "".join(symbol_lines)
        if (
            symbol_lines
            and re.search(r"[A-Za-z\u0370-\u03ff\ue000-\uf8ff]", symbol_text)
            and not all(_looks_like_pure_numeric_table_entry(line) for line in symbol_lines)
            and not _looks_like_missing_table_value(symbol_text)
            and all(
                len(line) <= 20
                and len(line.split()) <= 2
                and not re.search(r"(?i)\b(?:the|and|for|with|limit|length|coefficient)\b", line)
                for line in symbol_lines
            )
        ):
            symbol_hits += 1

        value_text = normalize_line(" ".join(value))
        if re.search(r"\d", value_text) or value_text.casefold() in {"yes", "no"}:
            value_hits += 1

        unit_lines = [normalize_line(line) for line in units if normalize_line(line)]
        if unit_lines and all(len(line) <= 14 and len(line.split()) <= 2 for line in unit_lines):
            unit_hits += 1
        if any(
            re.fullmatch(
                r"(?i)(?:—|-|GHz|MHz|kHz|Hz|UI(?:pp|RMS)?|V|mV|dB|Ω|ohms?|fF|pH|nF|mm|ns|ps|s|V2/GHz|1/mm|ns1/2/mm|%)",
                line,
            )
            for line in unit_lines
        ):
            known_unit_hits += 1

    row_count = len(four_column_rows)
    return (
        descriptor_hits / row_count >= 0.75
        and symbol_hits / row_count >= 0.85
        and value_hits / row_count >= 0.75
        and unit_hits / row_count >= 0.85
        and known_unit_hits >= 4
    )


def _expand_table_row(
    row: list[list[str]],
    header: list[str],
    *,
    cell_word_row: list[list[dict[str, object]]] | None = None,
) -> list[list[str]]:
    """Split a physical row while keeping the historical rows-only helper API."""

    expanded_rows, _alignment_reliable = _expand_table_row_with_evidence(
        row,
        header,
        cell_word_row=cell_word_row,
    )
    return expanded_rows


def _expand_table_row_with_evidence(
    row: list[list[str]],
    header: list[str],
    *,
    cell_word_row: list[list[dict[str, object]]] | None = None,
) -> tuple[list[list[str]], bool]:
    """Split one physical row and report whether every cross-row assignment is proven."""

    if not header:  # 没有列含义时不做列表展开，避免错拆普通多行说明。
        return [[_clean_table_cell_lines(cell_lines) for cell_lines in row]], True
    labels = _header_labels_for_row(header, len(row))  # 用当前行宽对齐表头，后续按列类型处理。
    column_values = [
        _logical_cell_values(cell_lines, labels[index])
        for index, cell_lines in enumerate(row)
    ]  # 每列先从物理行转换成逻辑值，例如 R/0/R/d 转成 R0、Rd。
    target_count = _table_row_expansion_count(
        column_values,
        labels,
        cell_word_row=cell_word_row,
    )  # 判断这一物理行是否包含多条参数记录。
    if target_count <= 1:
        repaired_values = _rejoin_aligned_descriptor_subscripts(
            row,
            labels,
            column_values,
        )
        if repaired_values is not None:
            column_values = repaired_values
            target_count = _table_row_expansion_count(
                column_values,
                labels,
                cell_word_row=cell_word_row,
            )
    if target_count <= 1:
        return (
            [[
                _clean_unexpanded_table_cell_lines(cell_lines, labels[index])
                for index, cell_lines in enumerate(row)
            ]],
            True,
        )  # 列内条数冲突时保留一条可审阅聚合行，不猜测删除或错位配对。

    sparse_unit_result = _geometry_aligned_sparse_unit_columns(
        column_values,
        labels,
        target_count,
        cell_word_row,
    )
    if sparse_unit_result is None:
        return (
            [[
                _clean_unexpanded_table_cell_lines(cell_lines, labels[index])
                for index, cell_lines in enumerate(row)
            ]],
            False,
        )
    sparse_unit_columns, sparse_unit_alignment_reliable = sparse_unit_result
    singleton_columns, singleton_alignment_reliable = _geometry_aligned_singleton_columns(
        column_values,
        target_count,
        cell_word_row,
        excluded_indexes=set(sparse_unit_columns),
    )
    for column_index, aligned_values in sparse_unit_columns.items():
        column_values[column_index] = aligned_values
    for column_index, aligned_values in singleton_columns.items():
        column_values[column_index] = aligned_values

    expanded_rows: list[list[str]] = []  # 收集拆分后的逻辑表格行。
    for row_index in range(target_count):
        expanded_rows.append(
            [
                _expanded_column_value(values, row_index, target_count, labels[index])
                for index, values in enumerate(column_values)
            ]
        )  # 每个逻辑行按同一序号从各列取值，缺失单元格保持空字符串。
    return (
        expanded_rows,
        sparse_unit_alignment_reliable and singleton_alignment_reliable,
    )


def _clean_unexpanded_table_cell_lines(lines: list[str], label: str) -> str:
    """Preserve logical boundaries when an ambiguous physical row cannot be expanded."""

    if _is_symbol_header(label):
        values = _symbol_lines_to_values(lines)
        return " / ".join(values)
    if _is_neutral_header(label):
        return "\n".join(lines)  # 用可逆 codec 保留物理换行；不可伪造字面 `/`，否则展平原文无法精确对齐。
    return _clean_table_cell_lines(lines)


def _logical_cell_values(lines: list[str], label: str) -> list[str]:
    """Convert physical cell lines into logical row values for one column."""

    if not lines:  # 空单元格参与对齐时保留一个空值。
        return [""]
    if _is_symbol_header(label):  # 符号列要先把上下标碎片合回完整符号。
        return _symbol_lines_to_values(lines)
    if _is_value_header(label):
        return _value_lines_to_values(lines)  # 数值列要剔除 pdfplumber 偶发抽出的单字母伪值。
    return lines  # 没有结构证据时逐项保留，不能把单字母或组标题猜成水印噪声。


def _rejoin_aligned_descriptor_subscripts(
    row: list[list[str]],
    labels: list[str],
    column_values: list[list[str]],
) -> list[list[str]] | None:
    """Repair compact identifiers only when other columns prove row alignment."""

    target_count = _aligned_non_descriptor_value_count(column_values, labels)
    if target_count <= 1:
        return None
    repaired_values = [list(values) for values in column_values]
    repaired = False
    for index, (lines, label) in enumerate(zip(row, labels, strict=False)):
        if not _is_descriptor_header(label):
            continue
        values = _descriptor_subscript_values(lines, target_count)
        if values is None:
            continue
        repaired_values[index] = values
        repaired = True
    return repaired_values if repaired else None


def _aligned_non_descriptor_value_count(
    columns: list[list[str]],
    labels: list[str],
) -> int:
    """Return a shared multi-value count independently observed in two columns."""

    counts = [
        len([value for value in values if value])
        for values, label in zip(columns, labels, strict=False)
        if not _is_descriptor_header(label)
        and len([value for value in values if value]) > 1
    ]
    if len(counts) < 2 or len(set(counts)) != 1:
        return 1
    return counts[0]


def _descriptor_subscript_values(
    lines: list[str],
    target_count: int,
) -> list[str] | None:
    """Rejoin base/subscript pairs in one descriptor cell with aligned evidence."""

    if (
        len(lines) == target_count + 1
        and not _looks_like_compact_descriptor_identifier(lines[0])
        and all(
            _looks_like_grouped_descriptor_identifier(value)
            for value in lines[1:]
        )
    ):
        values = list(lines[1:])
        values[0] = f"{lines[0]}\n{values[0]}"
        return values
    fragment_count = target_count * 2
    if len(lines) not in {fragment_count, fragment_count + 1}:
        return None
    group_label = lines[0] if len(lines) == fragment_count + 1 else ""
    fragments = lines[1:] if group_label else lines
    values: list[str] = []
    for index in range(0, len(fragments), 2):
        base = fragments[index]
        suffix = fragments[index + 1]
        if not _looks_like_descriptor_subscript_pair(base, suffix):
            return None
        values.append(base + suffix)
    if group_label:
        values[0] = f"{group_label}\n{values[0]}"
    return values


def _looks_like_descriptor_subscript_pair(base: str, suffix: str) -> bool:
    """Recognize compact engineering identifiers split at a visual subscript."""

    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,7}", base):
        return False
    return bool(
        re.fullmatch(r"\d+[A-Za-z0-9_.+-]{0,4}", suffix)
        or suffix == "RMS"
    )


def _looks_like_compact_descriptor_identifier(value: str) -> bool:
    """Recognize a joined identifier without classifying ordinary title words."""

    return bool(
        re.fullmatch(r"[A-Z][A-Z0-9_]*(?:\d+[a-z][A-Za-z0-9_.+-]*)?", value)
    )


def _looks_like_grouped_descriptor_identifier(value: str) -> bool:
    """Recognize record identifiers after a group label, never ordinary uppercase prose."""

    return bool(
        _looks_like_compact_descriptor_identifier(value)
        and (
            re.search(r"[0-9_().]", value)
            or value in {"JRMS", "EOJ", "JH", "RX", "TX", "COM", "SNDR"}
        )
    )  # MAXIMUM/LIKELIHOOD 与 MAXIMUM/OUTPUT 是软换行词组，不是记录 ID。


def _is_descriptor_header(label: str) -> bool:
    """Return True for columns whose cells name parameters or characteristics."""

    normalized = normalize_line(label).casefold().rstrip(".")
    return normalized in {
        "parameter",
        "parameters",
        "characteristic",
        "characteristics",
    }


def _is_neutral_header(label: str) -> bool:
    """Return True for generated labels that carry no column semantics."""

    return bool(re.fullmatch(r"(?i)column\s+\d+", normalize_line(label)))


def _neutral_symbol_fragment_values(lines: list[str]) -> list[str] | None:
    """Merge only strongly shaped base/subscript pairs in an unknown column."""

    if len(lines) < 2 or len(lines) % 2:
        return None
    values: list[str] = []
    for index in range(0, len(lines), 2):
        base = lines[index]
        suffix = lines[index + 1]
        if not re.fullmatch(r"[A-Za-zΔΓΤ\uf067\uf074]", base):
            return None
        if base == "\uf074":
            return None  # OIF Symbol-font τ 是独立参数；无几何证据时不能吞并后续 a/a1。
        if base == "\uf067" and not re.fullmatch(r"\d+", suffix):
            return None  # 已知 γ0 可由数字下标组成，但相邻字母属于下一参数。
        if re.fullmatch(r"\d+", suffix):
            values.append(base + suffix)
            continue
        suffix_match = re.fullmatch(r"([a-z]+)([0-9_.+-]{0,2})", suffix)
        if (
            suffix_match is None
            or not is_known_engineering_symbol_letter_suffix(
                base,
                suffix_match.group(1),
            )
        ):
            return None
        values.append(base + suffix)
    return values


def _is_symbol_header(label: str) -> bool:
    """Return True for headers that normally contain compact symbols."""

    normalized = normalize_line(label).casefold().rstrip(".")  # 大小写和句点不影响列类型判断。
    return normalized in {"symbol", "symbols", "sym", "parameter symbol"}  # 覆盖常见英文符号列写法。


def _is_value_header(label: str) -> bool:
    """Return True for columns that primarily contain numeric values."""

    normalized = normalize_line(label).casefold().rstrip(".")  # 表头大小写和句点不影响列类型。
    return normalized in {"value", "values", "min", "minimum", "typ", "typical", "max", "maximum"}  # 覆盖常见数值列。


def _is_structured_record_value_header(label: str) -> bool:
    """Return whether repeated values independently prove logical table rows."""

    normalized = normalize_line(label).casefold().rstrip(".")
    return (
        _is_symbol_header(label)
        or _is_value_header(label)
        or normalized in {"unit", "units"}
    )  # Parameter/Condition/Notes 都可能只是单元格内软换行，不能单独授权拆行。


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


def _looks_like_pure_numeric_table_entry(value: str) -> bool:
    """Return True only when a table entry is numeric rather than an identifier."""

    candidate = re.sub(r"\s+", "", normalize_line(value))
    if not re.search(r"\d", candidate):
        return False
    if re.fullmatch(
        r"[+\-−]?(?:\d+(?:\.\d+)?|\.\d+)(?:,[+\-−]?(?:\d+(?:\.\d+)?|\.\d+))+",
        candidate,
    ):
        return True  # 44,45 / 29,30 是一个单元格内的编号集合，不按逗号拆行但可证明该记录是原子值。

    unsigned_decimal = r"(?:\d+(?:,\d{3})*(?:\.\d*)?|\.\d+)"
    sign = r"[+\-−]?"
    number = (
        rf"{sign}{unsigned_decimal}"
        rf"(?:[eE]{sign}\d+|[xX×]10(?:\^)?{sign}\d+)?"
    )
    comparator = r"(?:[<>≤≥]=?|≈|~)?"
    range_suffix = rf"(?:(?:to|…|-|–|—){number})?"
    tolerance_suffix = rf"(?:±{number})?"
    return bool(
        re.fullmatch(
            rf"{comparator}{number}{range_suffix}{tolerance_suffix}%?",
            candidate,
        )
    )  # e/E 与 x/X 只在完整科学计数语法中放行；R0、zp2、c(0) 仍是标识符。


def _looks_like_missing_table_value(value: str) -> bool:
    """Return True for standard table placeholders that cannot prove a symbol column."""

    candidate = normalize_line(value).casefold()
    compact = re.sub(r"[\s._/\\-]+", "", candidate)
    return candidate in {"-", "–", "—"} or compact in {
        "na",
        "nr",
        "none",
        "notapplicable",
        "notavailable",
        "notreported",
        "notspecified",
        "tbc",
        "tbd",
        "unknown",
        "unavailable",
    }


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

    return _neutral_symbol_fragment_values([current, next_value]) is not None


def _table_row_expansion_count(
    columns: list[list[str]],
    labels: list[str],
    *,
    cell_word_row: list[list[dict[str, object]]] | None = None,
) -> int:
    """Return the likely logical row count represented by one physical row."""

    counts = [len([value for value in column if value]) for column in columns]  # 空值不应决定展开长度。
    multi_counts = [count for count in counts if count > 1]  # 至少两个多值列对齐时，才有足够证据拆行。
    if len(multi_counts) < 2:
        return 1
    count_support = Counter(multi_counts)
    supported_targets = [
        count
        for count, support in count_support.items()
        if support >= 2
        and not _table_columns_have_conflicting_geometry(
            columns,
            count,
            cell_word_row,
        )
        and _geometry_aligned_sparse_unit_columns(
            columns,
            labels,
            count,
            cell_word_row,
        )
        is not None
        and not _table_columns_have_ambiguous_arithmetic_continuation(
            columns,
            labels,
            count,
        )
        and not _table_columns_have_ambiguous_prose_continuation(
            columns,
            labels,
            count,
            cell_word_row=cell_word_row,
        )
        and (
            _numeric_and_secondary_structured_columns_prove_rows(
                columns,
                labels,
                count,
            )
            or _structured_record_columns_jointly_prove_rows(
                columns,
                labels,
                count,
            )
            or _neutral_record_columns_jointly_prove_rows(
                columns,
                labels,
                count,
            )
        )
        and all(
            (
                other == count
                if _is_symbol_header(label) or _is_descriptor_header(label)
                else other in {1, count}
            )
            or (
                other == count + 1
                and _is_descriptor_header(label)
                and _descriptor_group_label_proves_rows(values, count)
            )
            or (
                other == count - 1
                and normalize_line(label).casefold().rstrip(".") in {"unit", "units"}
            )
            for other, values, label in zip(counts, columns, labels, strict=False)
        )
    ]
    if len(supported_targets) != 1:
        return 1  # 必须由至少两列独立证明唯一行数；相差更多时仍保守保留聚合行。
    return supported_targets[0]


def _geometry_aligned_sparse_unit_columns(
    columns: list[list[str]],
    labels: list[str],
    target_count: int,
    cell_word_row: list[list[dict[str, object]]] | None,
) -> tuple[dict[int, list[str]], bool] | None:
    """Place sparse units and distinguish proven mapping from review-only fallback."""

    sparse_indexes: list[int] = []
    for index, (values, label) in enumerate(zip(columns, labels, strict=False)):
        non_empty_count = sum(bool(normalize_line(value)) for value in values)
        if (
            normalize_line(label).casefold().rstrip(".") in {"unit", "units"}
            and 0 < non_empty_count < target_count
        ):
            sparse_indexes.append(index)
    if not sparse_indexes:
        return {}, True
    if cell_word_row is None or len(cell_word_row) != len(columns):
        return None

    directly_aligned = _directly_aligned_sparse_unit_columns(
        columns,
        target_count,
        cell_word_row,
        sparse_indexes,
    )
    if directly_aligned is not None:
        direct_mapping_reliable = all(
            sum(bool(normalize_line(value)) for value in columns[index]) >= 2
            for index in sparse_indexes
        )
        return directly_aligned, direct_mapping_reliable
        # 单个 Unit 即使 y 唯一也可能是跨行共享单元格；可定位展示，但不得授权 confirmed 行差异或隐藏 bbox。
    fallback = _group_labeled_trailing_sparse_unit_columns(
        columns,
        labels,
        target_count,
        cell_word_row,
        sparse_indexes,
    )
    if fallback is None:
        return None
    return fallback, False  # 有序前缀可保留可读拆行，但不能授权隐藏原始 bbox 或宣称行归属可靠。


def _geometry_aligned_singleton_columns(
    columns: list[list[str]],
    target_count: int,
    cell_word_row: list[list[dict[str, object]]] | None,
    *,
    excluded_indexes: set[int],
) -> tuple[dict[int, list[str]], bool]:
    """Project singleton cells for readability without certifying row ownership.

    A singleton Conditions/Notes/Units cell may be a true row value or a merged
    cell spanning the whole physical group.  Repeating it across every logical
    row without cell-span evidence hides row-association changes.  A unique
    baseline match may localize the display to one row, while ambiguous or
    missing geometry keeps the historical broadcast.  Neither form proves the
    absence of a rowspan, so every expanded singleton keeps the whole physical
    group review-only and preserves the raw table bbox.
    """

    singleton_indexes = [
        index
        for index, values in enumerate(columns)
        if index not in excluded_indexes
        and len([value for value in values if normalize_line(value)]) == 1
    ]
    if not singleton_indexes:
        return {}, True
    if cell_word_row is None or len(cell_word_row) != len(columns):
        return {}, False

    reference_observations: list[list[tuple[float, float]]] = []
    for index, values in enumerate(columns):
        if index in singleton_indexes or index in excluded_indexes:
            continue
        normalized = [normalize_line(value) for value in values if normalize_line(value)]
        if len(normalized) != target_count:
            continue
        observations = _logical_value_geometry_observations(
            normalized,
            cell_word_row[index],
        )
        if observations is not None:
            reference_observations.append(observations)
    if len(reference_observations) < 2 or not _table_observation_columns_align(
        reference_observations,
        target_count,
    ):
        return {}, False

    reference_centers = [
        sum(observations[row_index][0] for observations in reference_observations)
        / len(reference_observations)
        for row_index in range(target_count)
    ]
    reference_heights = [
        min(observations[row_index][1] for observations in reference_observations)
        for row_index in range(target_count)
    ]
    aligned: dict[int, list[str]] = {}
    for index in singleton_indexes:
        values = [normalize_line(value) for value in columns[index] if normalize_line(value)]
        observations = _logical_value_geometry_observations(
            values,
            cell_word_row[index],
        )
        if observations is None or len(observations) != 1:
            continue
        center, height = observations[0]
        candidates = [
            row_index
            for row_index, (reference_center, reference_height) in enumerate(
                zip(reference_centers, reference_heights, strict=True)
            )
            if abs(center - reference_center)
            <= max(1.5, min(height, reference_height) * 0.45)
        ]
        if len(candidates) != 1:
            continue
        padded = [""] * target_count
        padded[candidates[0]] = values[0]
        aligned[index] = padded
    return aligned, False  # word y 不能区分单行值与跨行合并单元格，禁止据此产出 confirmed 差异。


def _directly_aligned_sparse_unit_columns(
    columns: list[list[str]],
    target_count: int,
    cell_word_row: list[list[dict[str, object]]],
    sparse_indexes: list[int],
) -> dict[int, list[str]] | None:
    """Map every sparse unit to one exact row baseline when direct geometry allows it."""

    reference_observations = [
        observations
        for index, observations in _direct_table_column_observations(
            columns,
            target_count,
            cell_word_row,
        )
        if index not in sparse_indexes
    ]
    if len(reference_observations) < 2 or not _table_observation_columns_align(
        reference_observations,
        target_count,
    ):
        return None
    reference_centers = [
        sum(observations[row_index][0] for observations in reference_observations)
        / len(reference_observations)
        for row_index in range(target_count)
    ]
    reference_heights = [
        min(observations[row_index][1] for observations in reference_observations)
        for row_index in range(target_count)
    ]
    aligned: dict[int, list[str]] = {}
    for index in sparse_indexes:
        values = [normalize_line(value) for value in columns[index] if normalize_line(value)]
        if not values or not all(_looks_like_known_atomic_unit(value) for value in values):
            return None
        observations = _geometry_line_observations(values, cell_word_row[index])
        if observations is None or len(observations) != len(values):
            return None
        padded = [""] * target_count
        used_rows: set[int] = set()
        for value, (center, height) in zip(values, observations, strict=True):
            candidates = [
                row_index
                for row_index, (reference_center, reference_height) in enumerate(
                    zip(reference_centers, reference_heights, strict=True)
                )
                if row_index not in used_rows
                and abs(center - reference_center)
                <= max(1.5, min(height, reference_height) * 0.45)
            ]
            if len(candidates) != 1:
                return None
            row_index = candidates[0]
            padded[row_index] = value
            used_rows.add(row_index)
        aligned[index] = padded
    return aligned


def _group_labeled_trailing_sparse_unit_columns(
    columns: list[list[str]],
    labels: list[str],
    target_count: int,
    cell_word_row: list[list[dict[str, object]]],
    sparse_indexes: list[int],
) -> dict[int, list[str]] | None:
    """Build a review-only ordered unit prefix with a trailing blank.

    A merged physical row can contain an explicit group label followed by N
    parameter records while its Units cell contains only the first N-1 values.
    Superscripts and subscripts make strict top-coordinate grouping unsuitable,
    so this path first reconstructs logical visual records losslessly.  The
    ordered prefix remains useful for display, but its caller always marks row
    assignment unreliable: relative line spacing cannot prove the omitted slot
    when baselines are non-uniform.
    """

    if target_count < 3:
        return None
    descriptor_candidates: list[tuple[int, list[tuple[float, float]]]] = []
    for index, (values, label) in enumerate(zip(columns, labels, strict=False)):
        non_empty = [normalize_line(value) for value in values if normalize_line(value)]
        if not (
            _is_descriptor_header(label)
            and _descriptor_group_label_proves_rows(non_empty, target_count)
        ):
            continue
        observations = _logical_value_geometry_observations(
            non_empty,
            cell_word_row[index],
        )
        if observations is not None and len(observations) == target_count + 1:
            descriptor_candidates.append((index, observations[1:]))
    if len(descriptor_candidates) != 1:
        return None
    descriptor_index, primary_observations = descriptor_candidates[0]

    typed_observations: list[list[tuple[float, float]]] = []
    for index, (values, label) in enumerate(zip(columns, labels, strict=False)):
        if index == descriptor_index or index in sparse_indexes:
            continue
        non_empty = [normalize_line(value) for value in values if normalize_line(value)]
        if len(non_empty) != target_count or not (
            _is_symbol_header(label) or _is_value_header(label)
        ):
            continue
        observations = _logical_value_geometry_observations(
            non_empty,
            cell_word_row[index],
        )
        if observations is not None and _table_observation_columns_align(
            [primary_observations, observations],
            target_count,
        ):
            typed_observations.append(observations)
    if not typed_observations:
        return None

    reference_observations = [primary_observations, *typed_observations]
    if not _table_observation_columns_align(reference_observations, target_count):
        return None
    reference_centers = [
        sum(observations[row_index][0] for observations in reference_observations)
        / len(reference_observations)
        for row_index in range(target_count)
    ]
    reference_heights = [
        min(observations[row_index][1] for observations in reference_observations)
        for row_index in range(target_count)
    ]

    aligned: dict[int, list[str]] = {}
    for index in sparse_indexes:
        values = [normalize_line(value) for value in columns[index] if normalize_line(value)]
        if (
            len(values) != target_count - 1
            or not all(_looks_like_known_atomic_unit(value) for value in values)
        ):
            return None
        observations = _logical_value_geometry_observations(
            values,
            cell_word_row[index],
        )
        if observations is None or len(observations) != target_count - 1:
            return None
        first_center, first_height = observations[0]
        first_tolerance = max(
            1.5,
            min(first_height, reference_heights[0]) * 0.45,
        )
        if abs(first_center - reference_centers[0]) > first_tolerance:
            return None  # 缺首项时首个 Unit 无法锚定逻辑首行，不能伪装成末尾空白。
        for row_index in range(len(observations) - 1):
            unit_gap = observations[row_index + 1][0] - observations[row_index][0]
            reference_gap = reference_centers[row_index + 1] - reference_centers[row_index]
            if unit_gap <= 0 or reference_gap <= 0:
                return None
            gap_ratio = unit_gap / reference_gap
            if not 0.55 <= gap_ratio <= 1.55:
                return None  # 中间漏项会形成接近双倍的跳距；只允许连续的前缀序列。
        aligned[index] = [*values, ""]
    return aligned


def _logical_value_geometry_observations(
    values: list[str],
    words: list[dict[str, object]],
) -> list[tuple[float, float]] | None:
    """Return logical record geometry after losslessly grouping script glyphs."""

    groups: list[tuple[float, float, list[dict[str, object]]]] = []
    ordered_words: list[tuple[float, float, float, dict[str, object]]] = []
    for word in words:
        try:
            top = float(word["top"])
            bottom = float(word["bottom"])
            x0 = float(word["x0"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (
            math.isfinite(top)
            and math.isfinite(bottom)
            and math.isfinite(x0)
            and bottom > top
            and normalize_line(str(word.get("text", "")))
        ):
            return None
        ordered_words.append((top, x0, bottom, word))

    for top, _x0, bottom, word in sorted(ordered_words):
        word_height = bottom - top
        candidates: list[tuple[float, int]] = []
        for group_index, (group_top, group_bottom, _group_words) in enumerate(groups):
            overlap = max(0.0, min(bottom, group_bottom) - max(top, group_top))
            minimum_height = min(word_height, group_bottom - group_top)
            if minimum_height > 0 and overlap >= minimum_height * 0.25:
                candidates.append((overlap / minimum_height, group_index))
        if candidates:
            _overlap_ratio, group_index = max(candidates)
            group_top, group_bottom, group_words = groups[group_index]
            group_words.append(word)
            groups[group_index] = (
                min(group_top, top),
                max(group_bottom, bottom),
                group_words,
            )
        else:
            groups.append((top, bottom, [word]))

    groups.sort(key=lambda group: group[0])
    expected = [
        re.sub(r"\s+", "", normalize_line(value))
        for value in values
    ]
    observed = [
        re.sub(
            r"\s+",
            "",
            normalize_line(
                _words_to_visual_line(
                    sorted(group_words, key=lambda word: float(word["x0"]))
                )
            ),
        )
        for _group_top, _group_bottom, group_words in groups
    ]
    if expected != observed:
        return None
    return [
        ((group_top + group_bottom) / 2, group_bottom - group_top)
        for group_top, group_bottom, _group_words in groups
    ]


def _table_columns_have_ambiguous_arithmetic_continuation(
    columns: list[list[str]],
    labels: list[str],
    target_count: int,
) -> bool:
    """Fail closed when a candidate row split can instead be one wrapped formula."""

    for values, label in zip(columns, labels, strict=False):
        normalized = [normalize_line(value) for value in values if normalize_line(value)]
        if (
            len(normalized) != target_count
            or not (_is_value_header(label) or _is_neutral_header(label))
        ):
            continue
        if any(
            (
                not _looks_like_missing_table_value(left)
                and not _looks_like_missing_table_value(right)
                and (
                    re.search(r"[+−×÷*/±=^·⋅<>≤≥≈~\-]\s*$", left)
                    or re.match(r"^\s*[+−×÷*/±=^·⋅<>≤≥≈~\-]", right)
                )
            )
            for left, right in zip(normalized, normalized[1:], strict=False)
        ):
            return True
    return False


def _table_columns_have_ambiguous_prose_continuation(
    columns: list[list[str]],
    labels: list[str],
    target_count: int,
    *,
    cell_word_row: list[list[dict[str, object]]] | None = None,
) -> bool:
    """Reject any unproved prose/formula column before another column can split it."""

    explicit_symbol_grid = any(
        _is_symbol_header(label)
        and len(normalized_values) == target_count
        and _symbol_record_values_prove_rows(normalized_values)
        for values, label in zip(columns, labels, strict=False)
        if (
            normalized_values := [
                normalize_line(value)
                for value in values
                if normalize_line(value)
            ]
        )
    )
    strong_typed_grid = (
        _structured_record_columns_jointly_prove_rows(
            columns,
            labels,
            target_count,
        )
        or (
            _numeric_and_secondary_structured_columns_prove_rows(
                columns,
                labels,
                target_count,
            )
            and explicit_symbol_grid
        )
    )  # 无 Unit 时仅让明确 Symbol+numeric 网格越过描述词歧义；纯 Min/Max 仍须描述列自证。
    prose_labels = {
        "condition",
        "conditions",
        "description",
        "descriptions",
        "note",
        "notes",
        "remark",
        "remarks",
        "formula",
        "formulas",
        "equation",
        "equations",
        "expression",
        "expressions",
    }
    for values, label in zip(columns, labels, strict=False):
        normalized = [normalize_line(value) for value in values if normalize_line(value)]
        normalized_label = normalize_line(label).casefold().rstrip(".")
        if len(normalized) != target_count:
            continue
        if normalized_label in prose_labels:
            return True
        if _is_value_header(label):
            mixed_typed_values = (
                strong_typed_grid
                and any(
                    _looks_like_atomic_categorical_value(value)
                    for value in normalized
                )
                and all(
                    _looks_like_pure_numeric_table_entry(value)
                    or _looks_like_missing_table_value(value)
                    or _looks_like_atomic_categorical_value(value)
                    for value in normalized
                )
                and not any(
                    re.match(r"^[+−×÷*/±=^·⋅<>≤≥≈~\-]", value)
                    and not _looks_like_missing_table_value(value)
                    for value in normalized[1:]
                )
            )
            if not (
                _numeric_record_values_prove_rows(normalized)
                or all(_looks_like_atomic_categorical_value(value) for value in normalized)
                or mixed_typed_values
            ):
                return True  # Maximum/Output、Equation/(31-1) 都可能只是同一单元格软换行。
            continue
        if _is_symbol_header(label):
            if not _symbol_record_values_prove_rows(normalized):
                return True  # JH/4u、JRMS/03 和普通 MAXIMUM/OUTPUT 都没有独立记录证据。
            continue
        if normalized_label in {"unit", "units"}:
            if not all(_looks_like_known_atomic_unit(value) for value in normalized):
                return True  # m/V、UI/rms、ns//mm 等均可能是一个单位的视觉换行。
            continue
        if _is_descriptor_header(label):
            if _descriptor_values_have_soft_wrap_evidence(normalized):
                return True
            if not (
                strong_typed_grid
                or _descriptor_record_values_prove_rows(normalized)
            ):
                return True
            continue
        if _is_neutral_header(label):
            if not (
                _numeric_record_values_prove_rows(normalized)
                or all(_looks_like_known_atomic_unit(value) for value in normalized)
                or _symbol_record_values_prove_rows(normalized)
                or _descriptor_record_values_prove_rows(normalized)
            ):
                return True  # 未知列的自然语言边界没有语义/几何证明时也必须保持聚合。
            continue
        return True  # Test Point 等未建模列没有逐行几何时，其他列不得替它授权拆分。
    return False


def _table_columns_have_conflicting_geometry(
    columns: list[list[str]],
    target_count: int,
    cell_word_row: list[list[dict[str, object]]] | None,
) -> bool:
    """Veto logical expansion when observed column baselines contradict it."""

    observations = _direct_table_column_observations(
        columns,
        target_count,
        cell_word_row,
    )
    return len(observations) >= 2 and not _table_observation_columns_align(
        [value for _index, value in observations],
        target_count,
    )


def _descriptor_geometry_proves_rows(
    columns: list[list[str]],
    labels: list[str],
    target_count: int,
    cell_word_row: list[list[dict[str, object]]] | None,
) -> bool:
    """Require a descriptor plus two typed columns on the same observed baselines."""

    observations = _direct_table_column_observations(
        columns,
        target_count,
        cell_word_row,
    )
    descriptor_indexes = {
        index
        for index, label in enumerate(labels)
        if _is_descriptor_header(label)
    }
    descriptor_observations = [
        value for index, value in observations if index in descriptor_indexes
    ]
    typed_observations = [
        value
        for index, value in observations
        if index not in descriptor_indexes
        and (
            _is_symbol_header(labels[index])
            or _is_value_header(labels[index])
            or normalize_line(labels[index]).casefold() in {"unit", "units"}
        )
    ]
    combined = [*descriptor_observations, *typed_observations]
    return (
        bool(descriptor_observations)
        and len(typed_observations) >= 2
        and _table_observation_columns_align(combined, target_count)
    )


def _direct_table_column_observations(
    columns: list[list[str]],
    target_count: int,
    cell_word_row: list[list[dict[str, object]]] | None,
) -> list[tuple[int, list[tuple[float, float]]]]:
    """Return geometry only for columns whose logical values map line-for-line."""

    if cell_word_row is None or len(cell_word_row) != len(columns):
        return []
    observed: list[tuple[int, list[tuple[float, float]]]] = []
    for index, values in enumerate(columns):
        normalized = [normalize_line(value) for value in values if normalize_line(value)]
        if len(normalized) != target_count:
            continue
        line_observations = _geometry_line_observations(
            normalized,
            cell_word_row[index],
        )
        if line_observations is not None:
            observed.append((index, line_observations))
    return observed


def _table_observation_columns_align(
    observations: list[list[tuple[float, float]]],
    target_count: int,
) -> bool:
    """Return whether every logical index shares one physical baseline."""

    if not observations:
        return False
    for row_index in range(target_count):
        centers = [column[row_index][0] for column in observations]
        heights = [column[row_index][1] for column in observations]
        tolerance = max(1.5, min(heights) * 0.45)
        if max(centers) - min(centers) > tolerance:
            return False
    return True


def _descriptor_values_have_soft_wrap_evidence(values: list[str]) -> bool:
    """Recognize descriptor line breaks that must never become logical rows."""

    if any(
        left.rstrip().endswith(("-", "‐", "‑", "‒", "–", "—"))
        for left in values[:-1]
    ):
        return True  # peak-to-\npeak 等显式断词绝不是两条参数记录。
    complete_wrapped_descriptor_patterns = (
        r"(?:the\s+)?maximum\s+likelihood",
        r"maximum\s+output",
        r"minimum\s+output",
        r"peak-to-?\s*peak",
        r"uncorrelated\s+jitter",
        r"correlated\s+jitter",
        r"standard\s+deviation",
        r"sequence\s+detection",
        r"characteristic\s+impedance",
        r"transmission\s+line",
        r"sinusoidal\s+jitter",
        r"reference\s+clock",
        r"random\s+jitter",
        r"target\s+detector",
        r"channel\s+operating",
        r"voltage\s+tolerance",
        r"noise\s+power",
        r"time\s+interval",
        r"(?:the\s+)?maximum\s+likelihood\s+sequence\s+detection(?:\s+\(mlsd\))?",
        r"sinusoidal\s+jitter,?\s+peak-to-?\s*peak(?:\s+\(ui\))?",
        r"uncorrelated\s+jitter\s+rms\s*\(\s*standard\s+deviation\s+of\s+the\s+"
        r"probability\s+distribution\s*\)",
        r"uncorrelated\s+jitter\s*\(\s*time\s+interval\s+from\s+0\.0025%\s+to\s+"
        r"99\.9975%\s+of\s+the\s+probability\s+distribution\s*\)",
        r"target\s+detector\s+error\s+ratio",
        r"channel\s+operating\s+margin(?:,?\s+min)?",
        r"noise\s+power\s+spectral\s+density(?:\s+\(v2/ghz\))?",
        r"time\s+interval\s+error(?:\s+\(tie\))?",
        r"reference\s+clock\s+phase\s+noise",
        r"random\s+jitter\s+rms",
        r"voltage\s+tolerance,?\s+maximum(?:\s+\(mv\))?",
        r"standard\s+deviation\s+of\s+the\s+probability\s+distribution",
    )
    for left, right in zip(values, values[1:], strict=False):
        left_stripped = left.strip()
        right_stripped = right.strip()
        left_folded = left_stripped.casefold()
        right_folded = right_stripped.casefold()
        joined_folded = compact_inline(f"{left_stripped} {right_stripped}").casefold()
        if any(
            re.fullmatch(pattern, joined_folded)
            for pattern in complete_wrapped_descriptor_patterns
        ):
            return True  # 整个描述语经任意断点合并后必须完整匹配，不能由邻列强网格误拆行。
        if re.match(r"^\d", right_folded) and re.search(
            r"(?:\bto|\bfrom|[<>=≤≥+−*/])\s*$",
            left_folded,
        ):
            return True  # 数字只在左侧以未完成范围/运算语法结尾时才是续行；10G 开头的真实参数仍可展开。
        if re.search(r"\bequations?\s*$", left_folded) and re.match(
            r"^\(\s*\d+(?:\s*[-‐‑‒–—−]\s*\d+)*\s*\)",
            right_folded,
        ):
            return True
    return False  # 只有术语真正跨越换行边界才否决拆行；完整参数行内含该术语仍可展开。


def _descriptor_record_values_prove_rows(values: list[str]) -> bool:
    """Require atomic labels or one shared descriptor prefix across candidate rows."""

    if len(values) < 2 or any(not value for value in values):
        return False
    if "\n" in values[0]:
        first_parts = [normalize_line(part) for part in values[0].splitlines() if normalize_line(part)]
        if (
            len(first_parts) == 2
            and len(first_parts[0]) >= 4
            and _looks_like_grouped_descriptor_identifier(first_parts[1])
            and all(
                _looks_like_grouped_descriptor_identifier(value)
                for value in values[1:]
            )
        ):
            return True  # Output Jitter\nJRMS + EOJ03/JH4u：首行是显式组名，后三列共同证明三条记录。
    first_variant_match = re.search(r"\s+[—–]\s+(.+)$", values[0])
    if first_variant_match is not None:
        variants = [first_variant_match.group(1), *values[1:]]
        variant_tokens = [variant.split() for variant in variants]
        if (
            all(len(tokens) >= 2 for tokens in variant_tokens)
            and len(
                {
                    tuple(token.casefold() for token in tokens[:-1])
                    for tokens in variant_tokens
                }
            )
            == 1
            and len({tokens[-1].casefold() for tokens in variant_tokens})
            == len(variant_tokens)
            and all(
                _looks_like_atomic_categorical_value(tokens[-1])
                for tokens in variant_tokens
            )
        ):
            return True
            # `... — Output Disabled\nOutput Enabled` 有显式变体前缀和闭集状态；保留首行完整主标题并安全拆成两条。
    if _descriptor_values_have_soft_wrap_evidence(values):
        return False
    token_rows = [value.split() for value in values]
    if all(len(tokens) == 1 for tokens in token_rows):
        identifiers = [
            re.fullmatch(r"([A-Za-z][A-Za-z_]*)[-_]?([0-9]+)", value)
            for value in values
        ]
        return bool(
            all(identifiers)
            and len({match.group(1).casefold() for match in identifiers if match}) == 1
            and len({match.group(2) for match in identifiers if match}) == len(values)
        )  # P1/P2 是可验证编号序列；任意两个单词都可能只是一个软换行术语。
    return bool(
        all(len(tokens) >= 2 for tokens in token_rows)
        and len({tokens[0].casefold() for tokens in token_rows}) == 1
        and len({tuple(token.casefold() for token in tokens) for tokens in token_rows})
        == len(token_rows)
    )  # Setting A/B 可证明；两段无共同边界的自然语言按一个软换行单元保留。


def _looks_like_independently_atomic_symbol_record(value: str) -> bool:
    """Recognize one complete symbol without accepting ordinary IN/AT words."""

    if not re.fullmatch(r"[^\s]{1,32}", value):
        return False
    if len(value) == 1:
        return not value.isdigit()
    if re.fullmatch(r"[A-Z][a-z]", value):
        return True  # Cb/Cp/Zc 等完整两字符工程符号；IN/AT 与普通长词不在此规则内。
    if value.casefold() in {
        "rx",
        "tx",
        "com",
        "sndr",
        "eoj",
        "jrms",
        "ffe",
        "dfe",
        "dcd",
        "isi",
    }:
        return True  # 仅放行协议表中高频且完整的技术缩写；普通 IN/AT 仍失败关闭。
    if (
        re.fullmatch(
            r"(?:[^\W\d_]|[\ue000-\uf8ff])[A-Za-z0-9_.()+\-/]{1,31}",
            value,
        )
        and re.search(r"[0-9_().]", value)
        and value.count("(") == value.count(")")
    ):
        return True  # JRMS03/a1/γ0 完整；4u/03 因不以字母或 PUA 基字符开头而拒绝。
    match = re.fullmatch(r"([A-Za-z])([a-z]+)", value)
    return bool(
        match
        and is_known_engineering_symbol_letter_suffix(
            match.group(1),
            match.group(2),
        )
    )


def _symbol_record_values_prove_rows(values: list[str]) -> bool:
    """Require complete adjacent symbols, not fragments of one wrapped identifier."""

    if len(values) < 2 or not all(
        _looks_like_independently_atomic_symbol_record(value)
        for value in values
    ):
        return False
    return not any(
        re.match(r"^[\d.)]", right)
        or left.count("(") != left.count(")")
        or bool(
            re.fullmatch(
                r"(?i)(?:T_)?(?:JH\d+(?:\.\d+)?u|JRMS\d+|EOJ\d+|SNR(?:ISI|TX))",
                left + right,
            )
        )
        or (
            len(left) > 1
            and bool(re.search(r"[\d_)]$", left))
            and bool(re.fullmatch(r"[a-z]{1,4}", right))
        )
        for left, right in zip(values, values[1:], strict=False)
    )  # JRMS0/3、JH4/u、Cd(/1) 都保留聚合；a/b、R1/R2、RX/TX 仍是完整记录。


def _looks_like_atomic_categorical_value(value: str) -> bool:
    """Recognize closed protocol enum values without accepting arbitrary prose words."""

    normalized = normalize_line(value).casefold()
    return bool(
        re.fullmatch(r"[a-z]", normalized)
        or normalized
        in {
            "yes",
            "no",
            "open",
            "closed",
            "enabled",
            "disabled",
            "required",
            "optional",
            "nrz",
            "pam4",
            "pass",
            "fail",
        }
    )


def _descriptor_group_label_proves_rows(values: list[str], target_count: int) -> bool:
    """Accept one extra descriptor only when its first line is an explicit group label."""

    non_empty = [normalize_line(value) for value in values if normalize_line(value)]
    return (
        len(non_empty) == target_count + 1
        and len(non_empty[0]) >= 4
        and bool(re.search(r"[:：]", non_empty[0]))
    )  # A/B/C/D/E 也可能是五条参数且缺一个值；显式冒号组名才授权折入首条记录。


def _numeric_and_secondary_structured_columns_prove_rows(
    columns: list[list[str]],
    labels: list[str],
    target_count: int,
) -> bool:
    """Require numeric records plus a second typed atomic column."""

    normalized_columns = [
        [normalize_line(value) for value in values if normalize_line(value)]
        for values in columns
    ]
    if any(
        _is_value_header(label)
        and len(values) == target_count
        and any(
            re.match(r"^[+−×÷*/±=^·⋅<>≤≥≈~\-]", value)
            and not _looks_like_missing_table_value(value)
            for value in values[1:]
        )
        for values, label in zip(normalized_columns, labels, strict=False)
    ):
        return False  # 任一数值列仍可解释为跨行公式时，其他两列也不能替它授权拆行。
    numeric_indexes = {
        index
        for index, (values, label) in enumerate(
            zip(normalized_columns, labels, strict=False)
        )
        if _is_value_header(label)
        and len(values) == target_count
        and _numeric_record_values_prove_rows(values)
    }
    if not numeric_indexes:
        return False
    if len(numeric_indexes) >= 2:
        return True  # 两个独立数值列逐行一致时已足以证明记录数；避免后续跳过 numeric index 令该分支不可达。
    for index, (values, label) in enumerate(
        zip(normalized_columns, labels, strict=False)
    ):
        if index in numeric_indexes or len(values) != target_count:
            continue
        if _is_value_header(label) and _numeric_record_values_prove_rows(values):
            return True
        if _is_symbol_header(label):
            if _symbol_record_values_prove_rows(values) and (
                all(len(value) == 1 for value in values)
                or all(re.search(r"[0-9_().]", value) for value in values)
            ):
                return True
        normalized_label = normalize_line(label).casefold().rstrip(".")
        if normalized_label in {"unit", "units"} and all(
            _looks_like_known_atomic_unit(value)
            for value in values
        ):
            return True
    return False


def _looks_like_known_atomic_unit(value: str) -> bool:
    """Return whether a compact cell is a known engineering unit token."""

    return bool(
        re.fullmatch(
            r"(?i)(?:—|-|"
            r"(?:T|G|M|k|m|u|µ|μ|n|p|f)?Hz|"
            r"UI(?:pp|RMS)?|"
            r"(?:k|m|u|µ|μ|n|p)?[AVW]|"
            r"(?:m|u|µ|μ|n|p|f)?F|"
            r"(?:m|u|µ|μ|n|p)?H|"
            r"dB(?:m|c)?|"
            r"(?:T|G|M|k)?(?:b/s|Bd)|"
            r"(?:m|u|µ|μ|n|p)?(?:Ω|ohms?)|"
            r"(?:km|cm|mm|um|µm|μm|nm)|"
            r"(?:ms|us|µs|μs|ns|ps|fs|s)|"
            r"ppm|ppb|°C|degC|"
            r"V2/GHz|1/mm|ns/mm|ns1/2/mm|%)",
            normalize_line(value),
        )
    )


def _numeric_record_values_prove_rows(values: list[str]) -> bool:
    """Require atomic numeric rows without a cross-line arithmetic continuation."""

    if len(values) < 2:
        return False
    if any(
        re.match(r"^[+−×÷*/±=^·⋅<>≤≥≈~\-]", value)
        and not _looks_like_missing_table_value(value)
        for value in values[1:]
    ):
        return False  # `1\n+2`/`1\n−2` 也可能是一条软换行公式；无其它列证据时不能猜成两条记录。
    return all(
        _looks_like_pure_numeric_table_entry(value)
        or _looks_like_missing_table_value(value)
        for value in values
    )


def _structured_record_columns_jointly_prove_rows(
    columns: list[list[str]],
    labels: list[str],
    target_count: int,
) -> bool:
    """Allow categorical rows only when Symbol, Value, and Unit all align atomically."""

    symbol_proven = False
    value_proven = False
    unit_proven = False
    for values, label in zip(columns, labels, strict=False):
        non_empty = [normalize_line(value) for value in values if normalize_line(value)]
        normalized_label = normalize_line(label).casefold().rstrip(".")
        is_unit_column = normalized_label in {"unit", "units"}
        permitted_count = (
            len(non_empty) in {target_count, target_count - 1}
            if is_unit_column
            else len(non_empty) == target_count
        )
        if not permitted_count or not _is_structured_record_value_header(label):
            continue
        if _is_symbol_header(label):
            atomic = _symbol_record_values_prove_rows(non_empty)
            symbol_proven = symbol_proven or atomic
        elif _is_value_header(label):
            atomic = all(
                _looks_like_pure_numeric_table_entry(value)
                or _looks_like_missing_table_value(value)
                or bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,15}", value))
                for value in non_empty
            )
            value_proven = value_proven or atomic
        elif is_unit_column:
            atomic = all(
                _looks_like_known_atomic_unit(value)
                for value in non_empty
            )
            unit_proven = unit_proven or atomic
        else:
            atomic = False
    return symbol_proven and value_proven and unit_proven
    # P1/P2/P3 + A/B/C + 完整单位是明确记录网格；V/REF + Maximum/Output + m/V 仍按软换行保留。


def _neutral_record_columns_jointly_prove_rows(
    columns: list[list[str]],
    labels: list[str],
    target_count: int,
) -> bool:
    """Require atomic numeric evidence before expanding an unknown-schema grid."""

    aligned_neutral_columns = [
        [normalize_line(value) for value in values if normalize_line(value)]
        for values, label in zip(columns, labels, strict=False)
        if _is_neutral_header(label)
        and len([value for value in values if normalize_line(value)]) == target_count
    ]
    numeric_columns = [
        values
        for values in aligned_neutral_columns
        if _numeric_record_values_prove_rows(values)
    ]
    repeated_unit_proven = any(
        len({value.casefold() for value in values}) == 1
        and _looks_like_known_atomic_unit(values[0])
        for values in aligned_neutral_columns
    )
    return len(aligned_neutral_columns) >= 3 and (
        len(numeric_columns) >= 2
        or (len(numeric_columns) == 1 and repeated_unit_proven)
    )  # 单个 numeric wrap 仍可能是公式；需第二数值列或重复单位列提供独立结构证据。


def _expanded_column_value(values: list[str], row_index: int, target_count: int, label: str) -> str:
    """Pick one aligned logical value from a possibly shorter or longer column."""

    if len(values) == target_count:
        return values[row_index]  # 几何填入的显式空位必须留在已证明的行，不能压缩后再左移。
    non_empty_values = [value for value in values if value]  # 展开时忽略纯空行，避免错位。
    if _is_value_header(label):
        non_empty_values = _drop_value_alignment_noise(non_empty_values, target_count)  # 只在行数对齐需要时剔除孤立伪值。
    if not non_empty_values:
        return ""
    if len(non_empty_values) == target_count:
        return non_empty_values[row_index]  # 标准情况：该列和逻辑行数量一致。
    if len(non_empty_values) == target_count + 1 and _is_descriptor_header(label):
        if row_index == 0:
            return f"{non_empty_values[0]}\n{non_empty_values[1]}"
        return non_empty_values[row_index + 1]
        # 参数列常比数值列多一个跨行 group label；将它归入首条而不是错移或丢弃末项。
    normalized_label = normalize_line(label).casefold().rstrip(".")
    if len(non_empty_values) == 1 and (
        normalized_label
        in {
            "condition",
            "conditions",
            "note",
            "notes",
            "remark",
            "remarks",
            "unit",
            "units",
        }
    ):
        return non_empty_values[0]  # 跨多条记录的条件、备注或单位对每条都适用，不能因 target=2 把第二条变空。
    if len(non_empty_values) == 1:
        return non_empty_values[0]  # 单个条件/备注值可复用于该物理行内所有逻辑记录。
    return non_empty_values[row_index] if row_index < len(non_empty_values) else " / ".join(non_empty_values)


def _drop_value_alignment_noise(values: list[str], target_count: int) -> list[str]:
    """Preserve opaque cell values when alignment alone cannot prove noise."""

    _ = target_count  # 行数差异是风险信号，不是删除某个值的充分证据。
    return values


def _looks_like_symbol_fragments(lines: list[str]) -> bool:
    """Detect short symbol/subscript fragments such as R + 0 or T_J + RMS."""

    return _neutral_symbol_fragment_values(lines) is not None


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

    cells = list(row)
    if not cells:  # 空行不输出。
        return ""
    if header:
        column_count = max(len(header), len(cells))
        labels = _header_labels_for_row(header, column_count)  # 表头长度可能短于数据行，需要补默认列名。
        cells.extend([""] * (column_count - len(cells)))
        parts = [
            encode_table_field(label, cell)
            for label, cell in zip(labels, cells, strict=False)
        ]  # 空字段也保留：全空列、重复表头位置和表结构本身都是可观察事实。
    else:
        parts = [escape_table_component(cell) for cell in cells]  # 没有表头时连同空列保留原始列顺序。
    if not parts:  # 完全没有列时跳过。
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


def _combine_text_and_table_lines(text: str, table_lines: list[str]) -> str:
    """Append structured table rows while preserving all observed raw text."""

    normalized_table_lines = [normalize_line(line) for line in table_lines if normalize_line(line)]  # 表格行先规整，仅用于精确去重。
    text_lines = [
        line
        for raw_line in text.splitlines()
        if (line := normalize_line(raw_line))
    ]  # 保留 PDF 文本层观测到的每一行，token 重叠不足以证明它与结构化表格同一。
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


def _finalize_extraction_result(
    *,
    path: Path,
    pages: list[PageText],
    warnings: list[str],
    total_pages: int,
    selected_start: int,
    selected_end: int,
    table_visuals: list[TableVisual] | None = None,
    formula_visuals: list[FormulaVisual] | None = None,
    source_sha256: str | None = None,
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
        formula_visuals=list(formula_visuals or []),
        source_sha256=source_sha256,
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
