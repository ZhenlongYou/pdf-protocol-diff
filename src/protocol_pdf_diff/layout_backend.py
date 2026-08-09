"""Optional high-fidelity layout-parser integration.

The native pdfplumber path remains the default because it is fast, deterministic
and already supplies the table screenshots used by reports.  A Docling result
is considered only for pages the native extractor has already marked as having
non-linear reading-order risk.  It must either be identical or prove that it
only moved complete unique prose lines without changing any technical token.
"""

from __future__ import annotations

import importlib.util
from importlib.metadata import PackageNotFoundError, version
from collections import Counter
from dataclasses import replace
from enum import Enum
from pathlib import Path
import re
from typing import Iterable, Sequence

from .models import ExtractionResult, PageText


class LayoutBackendMode(str, Enum):
    """User-selectable policy for the optional Docling layout parser."""

    NATIVE = "native"  # 默认值：完全保持当前 pdfplumber 快速路径。
    AUTO = "auto"  # 只在原生坐标已确认阅读顺序风险时，才尝试可选后端。
    DOCLING = "docling"  # 明确请求 Docling；仍保留安全门，不能盲目覆盖原文。


class LayoutBackendUnavailableError(ValueError):
    """Raised when an explicitly requested optional layout backend is absent."""


_MIN_CANDIDATE_CHARACTERS = 24
_NON_BODY_LABELS = frozenset(
    {"page_header", "page_footer", "caption", "picture"}
)  # formula 是可比较工程事实；只排除页边、题注和图片容器文字。


def normalize_layout_backend(value: str | LayoutBackendMode | None) -> LayoutBackendMode:
    """Convert a user value to a documented backend mode with a clear error."""

    if value is None:
        return LayoutBackendMode.NATIVE
    if isinstance(value, LayoutBackendMode):
        return value
    try:
        return LayoutBackendMode(str(value).strip().lower())
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in LayoutBackendMode)
        raise ValueError(f"版面解析后端必须是以下之一: {allowed}。") from exc


def should_attempt_docling(mode: LayoutBackendMode, pages: Sequence[PageText]) -> bool:
    """Return whether conversion is worth its cost for the extracted pages.

    Neither ``auto`` nor explicit ``docling`` converts ordinary linear pages:
    the optional parser is a repair path, not a second full parser for every
    report.  This is the primary guard against a normal-case performance drop.
    """

    if mode is LayoutBackendMode.NATIVE:
        return False
    return any(page.layout_risk and page.text.strip() for page in pages)


def choose_docling_page_text(
    native_text: str,
    candidate_text: str,
    *,
    layout_risk: bool,
    allow_whole_line_reordering: bool = False,
) -> str | None:
    """Accept identical text or a proved token-preserving whole-line reorder.

    Whitespace and line boundaries can carry section, table, code and literal
    structure.  The default therefore still requires every internal character
    to agree.  The enrichment path may explicitly allow reordered complete
    prose lines on a page already proven to have non-linear layout risk; the
    title, every line, case, punctuation, number, unit and occurrence count must
    remain exact.  Ordered lists, table-like rows and ambiguous duplicates fail
    closed to the native extraction.
    """

    if not layout_risk:
        return None
    candidate = candidate_text.strip()
    native = native_text.strip()
    if len(candidate) < _MIN_CANDIDATE_CHARACTERS or not native:
        return None
    if native == candidate:
        return candidate
    if allow_whole_line_reordering and _safe_whole_line_reordering(native, candidate):
        return candidate
    return None


_ORDERED_LINE_PREFIX_RE = re.compile(
    r"^(?:\(?\d+(?:\.\d+)*[.)]?|\(?[A-Za-z][.)]|[-*•])\s+"
)
_PROSE_LINE_END_RE = re.compile(r"[.!?。！？:]$")


def _safe_whole_line_reordering(native: str, candidate: str) -> bool:
    """Prove that Docling only moved unique, sentence-like complete lines."""

    native_lines = [_compact_line(line) for line in native.splitlines() if line.strip()]
    candidate_lines = [_compact_line(line) for line in candidate.splitlines() if line.strip()]
    if len(native_lines) < 4 or len(native_lines) != len(candidate_lines):
        return False
    # The leading heading anchors the page identity and cannot move between columns.
    if native_lines[0] != candidate_lines[0]:
        return False
    native_body = native_lines[1:]
    candidate_body = candidate_lines[1:]
    if native_body == candidate_body:
        return False
    if Counter(native_body) != Counter(candidate_body):
        return False
    # Duplicate lines cannot be mapped one-to-one, so their reordering is not provable.
    if any(count != 1 for count in Counter(native_body).values()):
        return False
    for line in native_body:
        if _ORDERED_LINE_PREFIX_RE.match(line):
            return False
        if not _PROSE_LINE_END_RE.search(line):
            return False
    return True


def _compact_line(value: str) -> str:
    """Normalize only whitespace inside one unchanged line."""

    return " ".join(value.split())


def enrich_with_optional_layout_backend(
    extraction: ExtractionResult,
    layout_backend: str | LayoutBackendMode | None,
) -> ExtractionResult:
    """Conservatively use Docling text for risky pages when the user opts in.

    ``native`` returns the original object without checking whether Docling is
    installed.  ``auto`` silently keeps native results when the optional
    package is absent, while explicit ``docling`` gives an actionable install
    error.  In all accepted cases ``layout_risk`` remains true so quality state
    never upgrades merely because a second parser agrees.
    """

    mode = normalize_layout_backend(layout_backend)
    if mode is LayoutBackendMode.NATIVE:
        return extraction
    if mode is LayoutBackendMode.DOCLING and not _docling_is_available():
        raise LayoutBackendUnavailableError(
            "已请求 Docling 版面解析，但当前环境未安装。"
            "请运行: python -m pip install -e '.[layout]'"
        )
    risky_pages = tuple(
        page.page_number
        for page in extraction.pages
        if page.layout_risk and page.text.strip()
    )
    if not should_attempt_docling(mode, extraction.pages):
        return extraction
    if not _docling_is_available():
        return extraction

    try:
        candidates = _extract_docling_page_texts(
            extraction.pdf_path,
            page_range=(min(risky_pages), max(risky_pages)),
        )
    except Exception as exc:
        if mode is LayoutBackendMode.DOCLING:
            raise LayoutBackendUnavailableError(
                f"Docling 版面解析失败: {exc}"
            ) from exc
        # auto 是可选增强；失败时保留原生证据且明确进入报告，不能吞掉事实。
        return replace(
            extraction,
            warnings=[
                *extraction.warnings,
                f"{extraction.pdf_path.name}: Docling 可选版面解析失败，已保留原生解析结果: {exc}",
            ],
        )

    pages: list[PageText] = []
    accepted_page_numbers: list[int] = []
    docling_version = _docling_version()
    for page in extraction.pages:
        selected = choose_docling_page_text(
            page.text,
            candidates.get(page.page_number, ""),
            layout_risk=page.layout_risk,
            allow_whole_line_reordering=True,
        )
        if selected is None:
            pages.append(page)
            continue
        # blocks 仍是原生坐标证据，不能把不同坐标系的 Docling bbox 混入同一审计字段。
        pages.append(
            replace(
                page,
                text=selected,
                comparison_text_source="docling",
                layout_backend_version=docling_version,
            )
        )
        accepted_page_numbers.append(page.page_number)

    if not accepted_page_numbers:
        return extraction
    page_label = ", ".join(str(number) for number in accepted_page_numbers)
    return replace(
        extraction,
        pages=pages,
        warnings=[
            *extraction.warnings,
            f"{extraction.pdf_path.name}: 已对阅读顺序风险页 {page_label} 使用通过一致性门的 Docling 文本；该页仍保持需人工复核。",
        ],
    )


def _docling_is_available() -> bool:
    """Check availability without importing model code or triggering downloads."""

    return importlib.util.find_spec("docling") is not None


def _docling_version() -> str | None:
    """Record the installed optional package version for reproducible audit."""

    try:
        return version("docling")
    except PackageNotFoundError:
        return None


def _extract_docling_page_texts(
    pdf_path: Path,
    *,
    page_range: tuple[int, int],
) -> dict[int, str]:
    """Convert only the smallest range containing risky pages and group its text."""

    from docling.document_converter import DocumentConverter

    # Docling's public API accepts one-based inclusive ``page_range``.  This
    # prevents a small review window from converting an entire long protocol.
    result = DocumentConverter().convert(str(pdf_path), page_range=page_range)
    document = result.document
    page_fragments: dict[int, list[str]] = {}
    for entry in _iterate_docling_items(document):
        item = entry[0] if isinstance(entry, tuple) else entry
        if _docling_item_label(item) in _NON_BODY_LABELS:
            continue
        text = _docling_item_text(item)
        if not text:
            continue
        for page_number in _docling_item_page_numbers(item):
            page_fragments.setdefault(page_number, []).append(text)
    return {
        page_number: "\n".join(fragments)
        for page_number, fragments in page_fragments.items()
    }


def _iterate_docling_items(document: object) -> Iterable[object]:
    """Use Docling's public traversal API and fail clearly on an incompatible API."""

    iterator = getattr(document, "iterate_items", None)
    if not callable(iterator):
        raise RuntimeError("Docling document does not expose iterate_items()")
    return iterator()


def _docling_item_label(item: object) -> str:
    """Normalize a Docling label without depending on a specific enum version."""

    label = getattr(item, "label", "")
    value = getattr(label, "value", label)
    return str(value).strip().lower()


def _docling_item_text(item: object) -> str:
    """Read plain/formula text; tables and images stay on the native screenshot path."""

    # 公式 item 的公开 ``text`` 必须与正文合并，再交给一致性门验证是否完整。
    text = getattr(item, "text", "")
    # 非字符串字段没有可比较语义，保持空值让调用方跳过该 item。
    return text.strip() if isinstance(text, str) else ""


def _docling_item_page_numbers(item: object) -> tuple[int, ...]:
    """Read page provenance defensively across supported Docling item versions."""

    provenance = getattr(item, "prov", ()) or ()
    page_numbers: list[int] = []
    for source in provenance:
        value = getattr(source, "page_no", None)
        try:
            page_number = int(value)
        except (TypeError, ValueError):
            continue
        if page_number > 0 and page_number not in page_numbers:
            page_numbers.append(page_number)
    return tuple(page_numbers)
