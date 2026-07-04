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


def extract_pdf_text(pdf_path: str | Path) -> ExtractionResult:
    """Extract selectable text from every page of a PDF.

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        ExtractionResult with one PageText per page and non-fatal warnings.

    Raises:
        FileNotFoundError: The PDF path does not exist.
        MissingDependencyError: pypdf is missing.
        PdfReadError: pypdf cannot open the file.
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

    pages: list[PageText] = []
    empty_pages: list[int] = []
    for index, page in enumerate(reader.pages, start=1):
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

    return ExtractionResult(pdf_path=path, pages=pages, warnings=warnings)
