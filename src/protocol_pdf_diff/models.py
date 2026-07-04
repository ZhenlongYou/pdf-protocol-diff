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


def _normalize_key(value: str) -> str:
    """Normalize a heading fragment for dictionary keys."""

    return " ".join(value.casefold().split())
