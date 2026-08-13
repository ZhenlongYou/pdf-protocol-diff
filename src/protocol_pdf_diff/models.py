"""Shared data models for the protocol comparison pipeline.

The models keep the extraction, sectioning, comparison, and reporting layers
loosely coupled. That separation matters for protocol work because PDF
extraction quality varies widely: a later version can swap in OCR or a stronger
layout parser while preserving the comparison/reporting behavior.
"""

from __future__ import annotations

import math  # 配置模型用有限性检查阻止 NaN/Inf 绕过章节匹配阈值。
from dataclasses import dataclass, field
from enum import Enum
from numbers import (
    Real,  # bool 虽是 int 子类，但不能作为相似度；Real 明确公共 API 的数值契约。
)
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .quality import DiffProvenance, PairAssessment


class DocumentBlockKind(str, Enum):
    """可审计文档块的来源类别，避免把 OCR 或表格伪装成原生正文。"""

    TEXT = "text"
    TABLE = "table"
    OCR = "ocr"


class PageParserRoute(str, Enum):
    """每页实际文字来源与已知版面风险的互斥审计分类。"""

    NATIVE_TEXT = "native_text"  # 线性原生文字页可由 pdfplumber 正常读取。
    NATIVE_LAYOUT_RISK = "native_layout_risk"  # 原生文字存在，但坐标证据提示阅读顺序可能非线性。
    OCR_FALLBACK = "ocr_fallback"  # 成功整页 OCR 的文字是降级证据，优先于其他路由事实。
    IMAGE_TEXT_LAYER = "image_text_layer"  # 大图像页仍带文字层，文字层可能不完整而不能自动判等。
    UNREADABLE_IMAGE = "unreadable_image"  # 大图像页既无有效文字层也未成功 OCR，必须显式留痕。


def classify_page_parser_route(
    *,
    text: str,
    layout_risk: bool,
    image_dominant: bool,
    ocr_used: bool,
) -> PageParserRoute:
    """从显式页面事实派生一个互斥且可审计的解析路由。"""

    # 成功 OCR 是最强的实际文字来源证据，必须覆盖其余所有页面特征。
    if ocr_used:
        return PageParserRoute.OCR_FALLBACK
    # 大面积图像页按是否仍存在文字层区分为可搜索层和无法读取两种路线。
    if image_dominant:
        return (
            PageParserRoute.IMAGE_TEXT_LAYER
            if text.strip()
            else PageParserRoute.UNREADABLE_IMAGE
        )
    # 非线性阅读顺序仅适用于仍以原生文字为主的页面。
    if layout_risk:
        return PageParserRoute.NATIVE_LAYOUT_RISK
    # 剩余情况是普通线性原生文字页。
    return PageParserRoute.NATIVE_TEXT


@dataclass(frozen=True)
class DocumentBlock:
    """一个带页面坐标和来源信息的不可变文档证据块。"""

    page_number: int  # 使用用户可见的 1-based 页码，便于回到 PDF 复核。
    bbox: tuple[float, float, float, float]  # 坐标采用 pdfplumber 的 (x0, top, x1, bottom) 页面坐标系。
    kind: DocumentBlockKind  # 明确区分原生文本、表格摘要和整页 OCR 证据。
    text: str  # 保存该块的可读内容，但不改变现有 PageText 的比较语义。
    reading_order: int  # 页内稳定序号从零开始连续编号，供后续版面解析器对照。
    source_engine: str  # 记录证据生产引擎，例如 pdfplumber 或 tesseract。
    confidence: float | None = None  # 未取得可靠置信度时必须为 None，禁止猜测 OCR 分数。


@dataclass(frozen=True)
class PageText:
    """Text extracted from one PDF page.

    Attributes:
        page_number: One-based page number, matching what a human sees in most
            PDF readers.
        text: Selectable text extracted from the page. Image-only/scanned pages
            usually produce an empty string and are reported as warnings.
    """

    page_number: int
    text: str
    layout_risk: bool = False
    ocr_used: bool = False
    blocks: tuple[DocumentBlock, ...] = ()  # 新字段默认空元组，保持旧构造调用和比较语义兼容。
    image_dominant: bool = False  # 图像覆盖判定独立于 OCR 是否实际执行，保留可搜索扫描页事实。
    parser_route: PageParserRoute = PageParserRoute.NATIVE_TEXT  # 默认原生路线保证旧 PageText 调用方保持兼容。
    comparison_text_source: str = "native"  # 实际参与正文比较的文字来源；不改变 parser_route 事实。
    layout_backend_version: str | None = None  # 可选后端实际采用时记录版本，便于严格复现。
    page_bbox: tuple[float, float, float, float] | None = None  # 原始页边界用于证明页边内容；缺失时禁止从文字包络猜测页面尺寸。
    ambiguous_line_number_sides: tuple[str, ...] = ()  # 疑似打印行号位于 left/right；数字保留，只供章节器抑制伪标题。
    visual_noise_bboxes: tuple[tuple[float, float, float, float], ...] = ()  # 仅保存坐标已证明并从比较文字过滤的页脚/页边噪声区域，视觉哨兵可据此精确屏蔽。
    running_header_texts: tuple[str, ...] = ()  # 跨页坐标证明的运行页眉从正文分离，但原文仍进入版本间结构化比较。

    def __post_init__(self) -> None:
        """Normalize the route so legacy and explicit constructions cannot contradict facts."""

        # 统一根据四项原始事实派生路由，避免旧默认值或显式陈旧值污染质量/报告审计。
        derived_route = classify_page_parser_route(
            text=self.text,
            layout_risk=self.layout_risk,
            image_dominant=self.image_dominant,
            ocr_used=self.ocr_used,
        )
        # frozen 数据类仍允许初始化时归一化；后续调用方不能再修改已建立的不变量。
        if self.parser_route != derived_route:
            object.__setattr__(self, "parser_route", derived_route)


@dataclass(frozen=True)
class PageExtractionAudit:
    """报告层所需的逐页解析快照，不保留原文、坐标块或图片。"""

    page_number: int  # 保留用户可见页码，供报告和人工复核定位。
    parser_route: PageParserRoute  # 记录互斥解析路线，不需要存放产生路线的文字内容。
    image_dominant: bool  # 保留图像主导事实，质量和报告可独立解释降级原因。
    ocr_used: bool  # 记录 OCR 是否真正执行成功，不能由 route 反向猜测。
    layout_risk: bool  # 保留原生阅读顺序风险，供审计区分图像和版面问题。
    block_count: int  # 只记录块数量，避免 DiffResult 保留 DocumentBlock 的正文与 bbox。
    comparison_text_source: str = "native"  # native 或通过安全门的 docling，便于 JSON 重放。
    layout_backend_version: str | None = None
    visual_noise_bbox_count: int = 0  # 只记录屏蔽区域数量，不泄漏坐标或页边文字。


@dataclass(frozen=True)
class ExtractionResult:
    """PDF extraction output plus non-fatal warnings.

    Warnings are carried into the final report because a reviewer needs to know
    when missing text may be caused by PDF structure rather than true document
    equality.
    ``selected_start_page`` and ``selected_end_page`` record the inclusive page
    window that was actually extracted. The original one-based page numbers are
    preserved in ``pages`` so reports still point back to the source PDF.
    """

    pdf_path: Path
    pages: list[PageText]
    warnings: list[str] = field(default_factory=list)
    total_pages: int = 0
    selected_start_page: int | None = None
    selected_end_page: int | None = None
    table_visuals: list["TableVisual"] = field(default_factory=list)  # 保存表格截图和识别摘要，供 HTML 报告展示视觉证据。
    source_sha256: str | None = None  # 解析入口对实际快照字节计算；禁止报告层事后重读路径伪装成同一输入。
    formula_visuals: list["FormulaVisual"] = field(default_factory=list)  # 新字段追加在旧位置参数之后，保存显示公式源截图和坐标语义。


def snapshot_page_extraction_audit(
    extraction: ExtractionResult,
) -> tuple[PageExtractionAudit, ...]:
    """Compress extraction pages into the immutable facts required by report audit."""

    # 逐页复制标量事实，返回后不再需要让 DiffResult 引用 ExtractionResult 或其页面列表。
    return tuple(
        PageExtractionAudit(
            page_number=page.page_number,
            parser_route=page.parser_route,
            image_dominant=page.image_dominant,
            ocr_used=page.ocr_used,
            layout_risk=page.layout_risk,
            block_count=len(page.blocks),
            comparison_text_source=page.comparison_text_source,
            layout_backend_version=page.layout_backend_version,
            visual_noise_bbox_count=len(page.visual_noise_bboxes),
        )
        for page in extraction.pages
    )


@dataclass(frozen=True)
class TableVisual:
    """One table-like visual region extracted from a PDF page."""

    page_number: int  # 使用源 PDF 的 1-based 页码，方便用户回到原文复核。
    table_number: int  # 同一页内的表格序号，用于生成稳定定位标签。
    title: str  # 从表格上方文本推断出的表题；没有表题时允许为空。
    bbox: tuple[float, float, float, float]  # pdfplumber 坐标系中的表格边界框。
    image_data_uri: str  # 内嵌 JPEG 截图，HTML 可以离线打开。
    row_texts: list[str]  # pdfplumber 抽取出的结构化表格行，用于行级摘要。
    grid_summary: str  # OpenCV 网格检测摘要，说明截图中横线/竖线证据强弱。
    ocr_text: str = ""  # 可选 OCR 文本；缺少 tesseract 引擎时保持为空。
    ocr_status: str = ""  # OCR 状态说明，必须明确成功、跳过或失败原因。
    is_continuation: bool = False  # 续页表格没有表题时，用该标记提示报告按上一表延续理解。
    page_bbox: tuple[float, float, float, float] | None = None  # 原页边界用于按页高比例证明真实跨页；旧调用缺省时不猜测。
    content_fully_represented: bool = False  # 只有 bbox 原文守恒且最终结构化行无内容损失时为真，供读者层去除重复表格正文。
    row_alignment_reliable: bool = False  # 多行单元格已取得可靠行对齐时为真；缺证据时禁止据此隐藏正文。


@dataclass(frozen=True)
class FormulaVisual:
    """One displayed equation backed by source pixels and word geometry."""

    page_number: int  # 源 PDF 的 1-based 页码。
    formula_number: str  # 源文显示的公式号，例如 ``(31-3)``。
    bbox: tuple[float, float, float, float]  # 公式、上下标和公式号的源页外接框。
    image_data_uri: str  # 无标注的源页 JPEG 裁剪，复杂根号/分式以它为准。
    source_text: str  # 坐标词的左到右原始摘要，不伪造 LaTeX。
    semantic_text: str  # 仅对几何已证明的上下标使用 ``_{} / ^{}`` 表示。
    script_count: int  # 已用字号、基线和水平邻接证明的上下标数。
    image_dhash: str = ""  # 64-bit dHash，只供相同文字层的视觉复核，不代替语义。


@dataclass(frozen=True)
class FormulaChange:
    """One paired or single-sided displayed-formula finding."""

    change_type: str  # modified / added / deleted / review。
    old_formula: FormulaVisual | None  # 旧版公式；新增项为 None。
    new_formula: FormulaVisual | None  # 新版公式；删除项为 None。
    similarity: float  # 上下标语义字符串的配对相似度。
    visual_similarity: float  # 源截图 dHash 相似度，只作复核证据。
    reason: str  # 面向读者的有限结论，不宣称全量数学 OCR。


@dataclass(frozen=True)
class VisualReviewItem:
    """One page-level visual delta that the semantic diff did not explain."""

    old_page_number: int | None  # 旧版源 PDF 的 1-based 页码；整页新增时为 None。
    new_page_number: int | None  # 新版源 PDF 的 1-based 页码；整页删除时为 None。
    change_type: str  # modified / added / deleted；始终属于复核证据而非已解释语义。
    pixel_similarity: float  # 对齐后像素相似度，仅用于排序和复核，不参与技术判等。
    changed_pixel_ratio: float  # 超过视觉阈值的页面像素比例。
    reason: str  # 明确说明为何需要回到源 PDF 核对。
    alignment_method: str = "same-page-text"  # 页面配对依据，供 JSON 审计与后续算法升级。
    diff_bbox: tuple[int, int, int, int] | None = None  # 差异像素在渲染图上的紧致外接框。
    old_image_data_uri: str = ""  # 旧页离线缩略图，仅供 HTML 人工复核。
    new_image_data_uri: str = ""  # 新页离线缩略图，仅供 HTML 人工复核。
    diff_image_data_uri: str = ""  # 差异掩膜预览，不伪装成语义结论。


@dataclass(frozen=True)
class VisualWatchdogAudit:
    """One run's immutable visual-watchdog coverage and source binding facts."""

    enabled: bool
    attempted: bool
    backend_available: bool | None
    eligible_page_pair_count: int
    checked_page_pair_count: int
    failed_page_pair_count: int
    ambiguous_page_count: int
    excluded_region_count: int
    complete: bool
    source_hashes_match: bool | None
    old_visual_source_sha256: str | None = None
    new_visual_source_sha256: str | None = None


@dataclass(frozen=True)
class TableRowChange:
    """One auditable row-level finding inside a paired table."""

    item: str  # 参数/特性名称，供导航、CSV 和人工复核使用。
    old_value: str  # 旧表中与该项目对应的符号、数值和单位摘要。
    new_value: str  # 新表中与该项目对应的符号、数值和单位摘要。
    change_type: str  # 用户可读分类，例如“实质变化”“新表新增行”。


@dataclass(frozen=True)
class TableChange:
    """One paired, added, or deleted logical table and its row findings."""

    change_type: str  # modified / added / deleted / review（仅结构不确定）。
    old_tables: tuple[TableVisual, ...]  # 同一旧版逻辑表格可能跨多个页面。
    new_tables: tuple[TableVisual, ...]  # 同一新版逻辑表格可能跨多个页面。
    similarity: float  # 表题和前几行形成的配对分数；单侧表格为 0。
    caption_changed: bool  # 表题或表号变化即使行内容相同也必须进入报告。
    row_changes: tuple[TableRowChange, ...]  # 完整行级事实，不受 HTML 展示上限影响。
    role: str = "technical"


@dataclass(frozen=True)
class HeadingInfo:
    """A heading detected from a protocol line.

    number is the structural marker (for example ``1.2`` or ``第一章``), title is
    the remaining human-readable heading text, and level controls the section
    stack. Numeric levels are inferred from dot depth where possible.
    """

    raw: str
    number: str
    title: str
    level: int


@dataclass(frozen=True)
class Section:
    """A logical protocol section used as the comparison unit.

    The section body intentionally excludes child headings only when the PDF has
    a clear hierarchy. In messy PDFs, the safest behavior is to preserve nearby
    text and report a page range so the reviewer can inspect the source.
    """

    section_id: str
    heading: str
    title: str
    level: int
    heading_path: tuple[str, ...]
    number_path: tuple[str, ...]
    start_page: int
    end_page: int
    body: str
    role: str = "technical"
    page_bodies: tuple[tuple[int, str], ...] = field(
        default=(),
        compare=False,
        repr=False,
    )  # 报告降噪需把片段绑定回原页；不参与历史 Section 身份与相等性语义。
    line_start_numbered_candidates: tuple[str, ...] = field(
        default=(),
        compare=False,
        repr=False,
    )  # 仅记录物理行首的后代编号候选，供读者层证明裸编号确来自标题式行而非句中 Version/ID。

    @property
    def location(self) -> str:
        """Human-facing chapter/section location for reports."""

        parts = [part for part in self.heading_path if part]
        return " / ".join(parts) if parts else self.heading

    @property
    def page_range(self) -> str:
        """Compact one-based page range."""

        if self.start_page == self.end_page:
            return str(self.start_page)
        return f"{self.start_page}-{self.end_page}"

    @property
    def comparable_text(self) -> str:
        """Text used for similarity matching.

        Heading plus body gives renamed sections a chance to match by content,
        while still favoring stable chapter/section numbers when available.
        """

        return f"{self.location}\n{self.body}".strip()

    @property
    def identity_key(self) -> str:
        """Stable structural key for exact section matching.

        Number paths are preferred because titles can change while section
        numbers remain stable. If the document has no section numbers, the
        normalized heading path becomes the fallback key.
        """

        if self.number_path:
            return " > ".join(self.number_path)
        return " > ".join(_normalize_key(part) for part in self.heading_path)


@dataclass(frozen=True)
class DiffOptions:
    """User-adjustable thresholds, page ranges, and output preferences.

    Page range values are one-based and inclusive. Leaving either side as
    ``None`` means "start at the first page" or "continue through the last page"
    for that PDF. Old and new ranges are intentionally independent so a reviewer
    can compare, for example, old pages 30-45 with new pages 34-51 after an
    inserted appendix shifts later content.
    """

    min_section_match_similarity: float = 0.72
    unchanged_similarity: float = 0.985  # Deprecated no-op retained only for older callers.
    max_snippets_per_section: int = 20
    include_unchanged_sections: bool = False
    old_start_page: int | None = None
    old_end_page: int | None = None
    new_start_page: int | None = None
    new_end_page: int | None = None
    ocr_language: str | None = None
    layout_backend: str = "native"  # 默认不启动重型版面解析，保护普通 PDF 的处理时间。
    visual_watchdog: bool = True  # 默认启用页级像素漏检哨兵；它只增加复核证据，不改写语义差异。

    def __post_init__(self) -> None:
        """Reject comparison settings that would silently disable matching."""

        threshold = self.min_section_match_similarity  # 统一读取 GUI、CLI 和 API 共用的章节匹配阈值。
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, Real)
            or not math.isfinite(threshold)
            or not 0.0 < threshold <= 1.0
        ):  # 相似度必须是有限的 (0, 1] 实数；bool/字符串不能借 Python 隐式类型规则绕过。
            raise ValueError("章节匹配阈值必须是 0 到 1 之间且大于 0 的有限数字。")  # 给调用方可直接展示的配置错误。


@dataclass(frozen=True)
class SnippetPair:
    """A replacement snippet showing old text beside new text."""

    old: str
    new: str


@dataclass(frozen=True)
class SectionChange:
    """One section-level difference to be written to reports."""

    change_type: str
    old_section: Section | None
    new_section: Section | None
    similarity: float
    added_snippets: list[str] = field(default_factory=list)
    removed_snippets: list[str] = field(default_factory=list)
    replaced_snippets: list[SnippetPair] = field(default_factory=list)
    omitted_snippet_count: int = 0
    match_basis: str = ""  # 追加在旧字段之后，保持历史位置参数调用兼容。
    audit_added_snippets: list[str] | None = None  # 机器审计保存限流前的全部 occurrence；None 表示旧调用沿用可见列表。
    audit_removed_snippets: list[str] | None = None
    audit_replaced_snippets: list[SnippetPair] | None = None

    @property
    def role(self) -> str:
        """Classify mixed changes conservatively as technical."""

        sections = (self.old_section, self.new_section)
        if any(section is not None and section.role == "technical" for section in sections):
            return "technical"
        return "document_metadata"

    @property
    def report_location(self) -> str:
        """Prefer the new location, falling back to the old deleted location."""

        section = self.new_section or self.old_section
        return section.location if section else "未知位置"


@dataclass(frozen=True)
class DiffResult:
    """Complete comparison result used by the reporting layer."""

    old_pdf: Path
    new_pdf: Path
    old_sections: list[Section]
    new_sections: list[Section]
    changes: list[SectionChange]
    warnings: list[str]
    old_total_pages: int = 0
    new_total_pages: int = 0
    old_selected_start_page: int | None = None
    old_selected_end_page: int | None = None
    new_selected_start_page: int | None = None
    new_selected_end_page: int | None = None
    old_table_visuals: list[TableVisual] = field(default_factory=list)  # 旧 PDF 的表格截图识别结果。
    new_table_visuals: list[TableVisual] = field(default_factory=list)  # 新 PDF 的表格截图识别结果。
    assessment: "PairAssessment | None" = None
    provenance: "DiffProvenance | None" = None
    old_extraction_audit: tuple[PageExtractionAudit, ...] = ()  # 旧版只保留无正文快照，完整 ExtractionResult 可在比较后释放。
    new_extraction_audit: tuple[PageExtractionAudit, ...] = ()  # 新版使用同样的轻量审计合同，避免长文档重复驻留内存。
    old_formula_visuals: list[FormulaVisual] = field(default_factory=list)  # 新字段追加在旧位置参数之后，保存旧 PDF 编号公式。
    new_formula_visuals: list[FormulaVisual] = field(default_factory=list)  # 新 PDF 的编号显示公式证据。
    formula_changes: list[FormulaChange] = field(default_factory=list)  # 公式语义、编号或视觉复核项。
    visual_review_items: list[VisualReviewItem] = field(default_factory=list)  # 语义层未覆盖的页级视觉变化，只作漏检哨兵。


def _normalize_key(value: str) -> str:
    """Normalize a heading fragment for dictionary keys."""

    return " ".join(value.casefold().split())
