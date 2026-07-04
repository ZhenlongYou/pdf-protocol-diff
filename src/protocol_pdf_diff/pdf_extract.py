"""PDF text extraction helpers.

This first version uses ``pypdf`` because it is lightweight and easy for a
PyCharm user to install. The tradeoff is important: pypdf can only extract text
that is actually encoded in the PDF. Scanned image PDFs, some CAD exports, and
badly embedded Chinese fonts may need OCR before this tool can compare them.
"""

from __future__ import annotations

from pathlib import Path

from .models import ExtractionResult, PageText


class MissingDependencyError(RuntimeError):
    """Raised when the user has not installed project requirements."""


class PdfReadError(RuntimeError):
    """Raised for PDF files that cannot be opened or decoded."""


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
        MissingDependencyError: pypdf is missing.
        PdfReadError: pypdf cannot open the file.
        ValueError: The requested page range is invalid for this PDF.
    """

    path = Path(pdf_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {path}")

    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError as PyPdfReadError
    except ModuleNotFoundError as exc:
        raise MissingDependencyError(
            "缺少依赖 pypdf。请在项目目录运行: python3 -m pip install -r requirements.txt"
        ) from exc

    warnings: list[str] = []
    try:
        reader = PdfReader(str(path))
    except PyPdfReadError as exc:
        raise PdfReadError(f"无法读取 PDF: {path}\n原因: {exc}") from exc

    if getattr(reader, "is_encrypted", False):
        try:
            decrypt_result = reader.decrypt("")
        except Exception as exc:  # pypdf can raise different backend errors.
            raise PdfReadError(f"PDF 已加密，且无法用空密码打开: {path}") from exc
        if not decrypt_result:
            raise PdfReadError(f"PDF 已加密，请先另存为未加密版本: {path}")
        warnings.append(f"{path.name}: PDF 已加密，已尝试使用空密码读取。")

    total_pages = len(reader.pages)
    selected_start, selected_end = _resolve_page_range(
        total_pages=total_pages,
        start_page=start_page,
        end_page=end_page,
        pdf_name=path.name,
    )
    pages: list[PageText] = []
    empty_pages: list[int] = []
    for index in range(selected_start, selected_end + 1):
        page = reader.pages[index - 1]
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # Keep the run useful even if one page is odd.
            text = ""
            warnings.append(f"{path.name}: 第 {index} 页文本抽取失败: {exc}")
        if not text.strip():
            empty_pages.append(index)
        pages.append(PageText(page_number=index, text=text))

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
