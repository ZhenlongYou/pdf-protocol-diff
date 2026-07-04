"""Shared data models for the protocol comparison pipeline.

The models keep the extraction, sectioning, comparison, and reporting layers
loosely coupled. That separation matters for protocol work because PDF
extraction quality varies widely: a later version can swap in OCR or a stronger
layout parser while preserving the comparison/reporting behavior.
"""

# Codex说明(自动生成)： 从 __future__ 导入 annotations，启用较新的类型标注行为，减少运行期导入或前向引用问题。
from __future__ import annotations

# Codex说明(自动生成)： 从 dataclasses 导入 dataclass, field，声明轻量数据结构并减少样板初始化代码。
from dataclasses import dataclass, field
# Codex说明(自动生成)： 从 pathlib 导入 Path，用 Path 对象处理跨平台文件路径。
from pathlib import Path


# Codex说明(自动生成)： 定义 PageText 类，把相关数据结构、校验规则或操作方法组织在一起。
@dataclass(frozen=True)
class PageText:
    """Text extracted from one PDF page.

    Attributes:
        page_number: One-based page number, matching what a human sees in most
            PDF readers.
        text: Selectable text extracted from the page. Image-only/scanned pages
            usually produce an empty string and are reported as warnings.
    """

    # Codex说明(自动生成)： 声明并保存 page_number，同时保留类型信息方便维护和静态检查。
    page_number: int
    # Codex说明(自动生成)： 声明并保存 text，同时保留类型信息方便维护和静态检查。
    text: str


# Codex说明(自动生成)： 定义 ExtractionResult 类，把相关数据结构、校验规则或操作方法组织在一起。
@dataclass(frozen=True)
class ExtractionResult:
    """PDF extraction output plus non-fatal warnings.

    Warnings are carried into the final report because a reviewer needs to know
    when missing text may be caused by PDF structure rather than true document
    equality.
    """

    # Codex说明(自动生成)： 声明并保存 pdf_path，同时保留类型信息方便维护和静态检查。
    pdf_path: Path
    # Codex说明(自动生成)： 声明并保存 pages，同时保留类型信息方便维护和静态检查。
    pages: list[PageText]
    # Codex说明(自动生成)： 声明并保存 warnings，同时保留类型信息方便维护和静态检查。
    warnings: list[str] = field(default_factory=list)


# Codex说明(自动生成)： 定义 HeadingInfo 类，把相关数据结构、校验规则或操作方法组织在一起。
@dataclass(frozen=True)
class HeadingInfo:
    """A heading detected from a protocol line.

    number is the structural marker (for example ``1.2`` or ``第一章``), title is
    the remaining human-readable heading text, and level controls the section
    stack. Numeric levels are inferred from dot depth where possible.
    """

    # Codex说明(自动生成)： 声明并保存 raw，同时保留类型信息方便维护和静态检查。
    raw: str
    # Codex说明(自动生成)： 声明并保存 number，同时保留类型信息方便维护和静态检查。
    number: str
    # Codex说明(自动生成)： 声明并保存 title，同时保留类型信息方便维护和静态检查。
    title: str
    # Codex说明(自动生成)： 声明并保存 level，同时保留类型信息方便维护和静态检查。
    level: int


# Codex说明(自动生成)： 定义 Section 类，把相关数据结构、校验规则或操作方法组织在一起。
@dataclass(frozen=True)
class Section:
    """A logical protocol section used as the comparison unit.

    The section body intentionally excludes child headings only when the PDF has
    a clear hierarchy. In messy PDFs, the safest behavior is to preserve nearby
    text and report a page range so the reviewer can inspect the source.
    """

    # Codex说明(自动生成)： 声明并保存 section_id，同时保留类型信息方便维护和静态检查。
    section_id: str
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
    # Codex说明(自动生成)： 声明并保存 body，同时保留类型信息方便维护和静态检查。
    body: str

    # Codex说明(自动生成)： 定义函数 location，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
    @property
    def location(self) -> str:
        """Human-facing chapter/section location for reports."""

        # Codex说明(自动生成)： 计算并保存 parts，供后续语句继续读取或更新。
        parts = [part for part in self.heading_path if part]
        # Codex说明(自动生成)： 返回 ' / '.join(parts) if parts else self.heading，让调用方取得本函数的处理结果。
        return " / ".join(parts) if parts else self.heading

    # Codex说明(自动生成)： 定义函数 page_range，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
    @property
    def page_range(self) -> str:
        """Compact one-based page range."""

        # Codex说明(自动生成)： 检查条件 self.start_page == self.end_page，根据结果选择后续执行路径。
        if self.start_page == self.end_page:
            # Codex说明(自动生成)： 返回 str(self.start_page)，让调用方取得本函数的处理结果。
            return str(self.start_page)
        # Codex说明(自动生成)： 返回 f'{self.start_page}-{self.end_page}'，让调用方取得本函数的处理结果。
        return f"{self.start_page}-{self.end_page}"

    # Codex说明(自动生成)： 定义函数 comparable_text，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
    @property
    def comparable_text(self) -> str:
        """Text used for similarity matching.

        Heading plus body gives renamed sections a chance to match by content,
        while still favoring stable chapter/section numbers when available.
        """

        # Codex说明(自动生成)： 返回 f'{self.location}\n{self.body}'.strip()，让调用方取得本函数的处理结果。
        return f"{self.location}\n{self.body}".strip()

    # Codex说明(自动生成)： 定义函数 identity_key，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
    @property
    def identity_key(self) -> str:
        """Stable structural key for exact section matching.

        Number paths are preferred because titles can change while section
        numbers remain stable. If the document has no section numbers, the
        normalized heading path becomes the fallback key.
        """

        # Codex说明(自动生成)： 检查条件 self.number_path，根据结果选择后续执行路径。
        if self.number_path:
            # Codex说明(自动生成)： 返回 ' > '.join(self.number_path)，让调用方取得本函数的处理结果。
            return " > ".join(self.number_path)
        # Codex说明(自动生成)： 返回 ' > '.join((_normalize_key(part) for part in self.headi...，让调用方取得本函数的处理结果。
        return " > ".join(_normalize_key(part) for part in self.heading_path)


# Codex说明(自动生成)： 定义 DiffOptions 类，把相关数据结构、校验规则或操作方法组织在一起。
@dataclass(frozen=True)
class DiffOptions:
    """User-adjustable thresholds and output preferences."""

    # Codex说明(自动生成)： 声明并保存 min_section_match_similarity，同时保留类型信息方便维护和静态检查。
    min_section_match_similarity: float = 0.72
    # Codex说明(自动生成)： 声明并保存 unchanged_similarity，同时保留类型信息方便维护和静态检查。
    unchanged_similarity: float = 0.985
    # Codex说明(自动生成)： 声明并保存 max_snippets_per_section，同时保留类型信息方便维护和静态检查。
    max_snippets_per_section: int = 8
    # Codex说明(自动生成)： 声明并保存 include_unchanged_sections，同时保留类型信息方便维护和静态检查。
    include_unchanged_sections: bool = False


# Codex说明(自动生成)： 定义 SnippetPair 类，把相关数据结构、校验规则或操作方法组织在一起。
@dataclass(frozen=True)
class SnippetPair:
    """A replacement snippet showing old text beside new text."""

    # Codex说明(自动生成)： 声明并保存 old，同时保留类型信息方便维护和静态检查。
    old: str
    # Codex说明(自动生成)： 声明并保存 new，同时保留类型信息方便维护和静态检查。
    new: str


# Codex说明(自动生成)： 定义 SectionChange 类，把相关数据结构、校验规则或操作方法组织在一起。
@dataclass(frozen=True)
class SectionChange:
    """One section-level difference to be written to reports."""

    # Codex说明(自动生成)： 声明并保存 change_type，同时保留类型信息方便维护和静态检查。
    change_type: str
    # Codex说明(自动生成)： 声明并保存 old_section，同时保留类型信息方便维护和静态检查。
    old_section: Section | None
    # Codex说明(自动生成)： 声明并保存 new_section，同时保留类型信息方便维护和静态检查。
    new_section: Section | None
    # Codex说明(自动生成)： 声明并保存 similarity，同时保留类型信息方便维护和静态检查。
    similarity: float
    # Codex说明(自动生成)： 声明并保存 added_snippets，同时保留类型信息方便维护和静态检查。
    added_snippets: list[str] = field(default_factory=list)
    # Codex说明(自动生成)： 声明并保存 removed_snippets，同时保留类型信息方便维护和静态检查。
    removed_snippets: list[str] = field(default_factory=list)
    # Codex说明(自动生成)： 声明并保存 replaced_snippets，同时保留类型信息方便维护和静态检查。
    replaced_snippets: list[SnippetPair] = field(default_factory=list)

    # Codex说明(自动生成)： 定义函数 report_location，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
    @property
    def report_location(self) -> str:
        """Prefer the new location, falling back to the old deleted location."""

        # Codex说明(自动生成)： 计算并保存 section，供后续语句继续读取或更新。
        section = self.new_section or self.old_section
        # Codex说明(自动生成)： 返回 section.location if section else '未知位置'，让调用方取得本函数的处理结果。
        return section.location if section else "未知位置"


# Codex说明(自动生成)： 定义 DiffResult 类，把相关数据结构、校验规则或操作方法组织在一起。
@dataclass(frozen=True)
class DiffResult:
    """Complete comparison result used by the reporting layer."""

    # Codex说明(自动生成)： 声明并保存 old_pdf，同时保留类型信息方便维护和静态检查。
    old_pdf: Path
    # Codex说明(自动生成)： 声明并保存 new_pdf，同时保留类型信息方便维护和静态检查。
    new_pdf: Path
    # Codex说明(自动生成)： 声明并保存 old_sections，同时保留类型信息方便维护和静态检查。
    old_sections: list[Section]
    # Codex说明(自动生成)： 声明并保存 new_sections，同时保留类型信息方便维护和静态检查。
    new_sections: list[Section]
    # Codex说明(自动生成)： 声明并保存 changes，同时保留类型信息方便维护和静态检查。
    changes: list[SectionChange]
    # Codex说明(自动生成)： 声明并保存 warnings，同时保留类型信息方便维护和静态检查。
    warnings: list[str]


# Codex说明(自动生成)： 定义函数 _normalize_key，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def _normalize_key(value: str) -> str:
    """Normalize a heading fragment for dictionary keys."""

    # Codex说明(自动生成)： 返回 ' '.join(value.casefold().split())，让调用方取得本函数的处理结果。
    return " ".join(value.casefold().split())
