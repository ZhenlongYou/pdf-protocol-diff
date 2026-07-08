"""Shared data models for the protocol comparison pipeline.

The models keep the extraction, sectioning, comparison, and reporting layers
loosely coupled. That separation matters for protocol work because PDF
extraction quality varies widely: a later version can swap in OCR or a stronger
layout parser while preserving the comparison/reporting behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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


@dataclass(frozen=True)
class LayoutLine:
    """One reconstructed visual text line from PDF spans."""

    page_number: int  # 源 PDF 页码，使用 1-based 编号，便于报告定位。
    text: str  # 该行所有 span 按 x 坐标合并后的可读文本。
    bbox: tuple[float, float, float, float]  # 行在页面坐标系中的边界框。
    font: str  # 该行主要字体名，用于段落合并和标题判断。
    size: float  # 该行加权平均字号，用于段落合并和标题判断。
    span_count: int = 1  # 该行包含的 span 数量，多列/表格行通常更高。
    baseline_spread: float = 0.0  # span y 坐标离散度，高离散度常见于公式上下标。
    page_width: float = 0.0  # 页面宽度，供居中公式和页眉页脚判断使用。
    page_height: float = 0.0  # 页面高度，供页眉页脚判断使用。


@dataclass(frozen=True)
class LayoutRegion:
    """A typed page region used by the layout-aware hybrid diff."""

    region_id: str  # 稳定区域 ID，包含页码、类型和顺序号，便于 HTML 锚点跳转。
    region_type: str  # paragraph、heading、table、formula、figure 或 header_footer。
    page_number: int  # 源 PDF 页码。
    bbox: tuple[float, float, float, float]  # 区域边界框，使用 PDF 页面坐标。
    text: str = ""  # 文本区域的段落内容；视觉区域只存少量 caption/context。
    section_context: str = ""  # 最近标题或上下文，用于跨页偏移时辅助匹配。
    order_index: int = 0  # 文档内顺序号，用于同分匹配时保持阅读顺序。
    image_hash: str = ""  # 视觉区域截图的平均哈希，用于表格/公式/图片匹配。
    page_width: float = 0.0  # 页面宽度，报告和 bbox 归一化需要。
    page_height: float = 0.0  # 页面高度，报告和 bbox 归一化需要。


@dataclass(frozen=True)
class RegionChange:
    """One layout-aware region difference for the HTML report."""

    change_type: str  # added、removed 或 modified。
    region_type: str  # paragraph、heading、table、formula、figure。
    old_region: LayoutRegion | None  # 旧版区域；新增时为空。
    new_region: LayoutRegion | None  # 新版区域；删除时为空。
    similarity: float  # 文本或图像综合相似度。
    old_image_data_uri: str = ""  # 视觉区域旧截图；文本区域通常为空。
    new_image_data_uri: str = ""  # 视觉区域新截图；文本区域通常为空。
    diff_image_data_uri: str = ""  # 视觉差异热图；新增/删除或文本区域可为空。


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
    unchanged_similarity: float = 0.985
    max_snippets_per_section: int = 20
    include_unchanged_sections: bool = False
    old_start_page: int | None = None
    old_end_page: int | None = None
    new_start_page: int | None = None
    new_end_page: int | None = None


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
    region_changes: list[RegionChange] = field(default_factory=list)  # layout-aware 区域差异，HTML 报告优先使用。


def _normalize_key(value: str) -> str:
    """Normalize a heading fragment for dictionary keys."""

    return " ".join(value.casefold().split())
