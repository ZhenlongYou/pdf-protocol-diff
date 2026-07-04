"""PDF text extraction helpers.

This first version uses ``pypdf`` because it is lightweight and easy for a
PyCharm user to install. The tradeoff is important: pypdf can only extract text
that is actually encoded in the PDF. Scanned image PDFs, some CAD exports, and
badly embedded Chinese fonts may need OCR before this tool can compare them.
"""

# Codex说明(自动生成)： 从 __future__ 导入 annotations，启用较新的类型标注行为，减少运行期导入或前向引用问题。
from __future__ import annotations

# Codex说明(自动生成)： 从 pathlib 导入 Path，用 Path 对象处理跨平台文件路径。
from pathlib import Path

# Codex说明(自动生成)： 从 models 导入 ExtractionResult, PageText，提供本文件后续流程需要的库能力。
from .models import ExtractionResult, PageText


# Codex说明(自动生成)： 定义 MissingDependencyError 类，把相关数据结构、校验规则或操作方法组织在一起。
class MissingDependencyError(RuntimeError):
    """Raised when the user has not installed project requirements."""


# Codex说明(自动生成)： 定义 PdfReadError 类，把相关数据结构、校验规则或操作方法组织在一起。
class PdfReadError(RuntimeError):
    """Raised for PDF files that cannot be opened or decoded."""


# Codex说明(自动生成)： 定义函数 extract_pdf_text，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
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

    # Codex说明(自动生成)： 计算并保存 path，供后续语句继续读取或更新。
    path = Path(pdf_path).expanduser().resolve()
    # Codex说明(自动生成)： 检查条件 not path.exists()，根据结果选择后续执行路径。
    if not path.exists():
        # Codex说明(自动生成)： 抛出 FileNotFoundError(f'PDF 文件不存在: {path}')，明确提示输入、状态或处理流程无法继续。
        raise FileNotFoundError(f"PDF 文件不存在: {path}")

    # Codex说明(自动生成)： 开始执行可能失败的代码块，并把异常、收尾或兜底逻辑交给后续分支处理。
    try:
        # Codex说明(自动生成)： 从 pypdf 导入 PdfReader，提供本文件后续流程需要的库能力。
        from pypdf import PdfReader
        # Codex说明(自动生成)： 从 pypdf.errors 导入 PyPdfReadError，提供本文件后续流程需要的库能力。
        from pypdf.errors import PdfReadError as PyPdfReadError
    # Codex说明(自动生成)： 捕获 ModuleNotFoundError，执行对应的恢复、记录或重新报错逻辑。
    except ModuleNotFoundError as exc:
        # Codex说明(自动生成)： 抛出 MissingDependencyError('缺少依赖 pypdf。请在项目目录运行: python3 -m...，明确提示输入、状态或处理流程无法继续。
        raise MissingDependencyError(
            "缺少依赖 pypdf。请在项目目录运行: python3 -m pip install -r requirements.txt"
        ) from exc

    # Codex说明(自动生成)： 声明并保存 warnings，同时保留类型信息方便维护和静态检查。
    warnings: list[str] = []
    # Codex说明(自动生成)： 开始执行可能失败的代码块，并把异常、收尾或兜底逻辑交给后续分支处理。
    try:
        # Codex说明(自动生成)： 计算并保存 reader，供后续语句继续读取或更新。
        reader = PdfReader(str(path))
    # Codex说明(自动生成)： 捕获 PyPdfReadError，执行对应的恢复、记录或重新报错逻辑。
    except PyPdfReadError as exc:
        # Codex说明(自动生成)： 抛出 PdfReadError(f'无法读取 PDF: {path}\n原因: {exc}')，明确提示输入、状态或处理流程无法继续。
        raise PdfReadError(f"无法读取 PDF: {path}\n原因: {exc}") from exc

    # Codex说明(自动生成)： 检查条件 getattr(reader, 'is_encrypted', False)，根据结果选择后续执行路径。
    if getattr(reader, "is_encrypted", False):
        # Codex说明(自动生成)： 开始执行可能失败的代码块，并把异常、收尾或兜底逻辑交给后续分支处理。
        try:
            # Codex说明(自动生成)： 计算并保存 decrypt_result，供后续语句继续读取或更新。
            decrypt_result = reader.decrypt("")
        # Codex说明(自动生成)： 捕获 Exception，执行对应的恢复、记录或重新报错逻辑。
        except Exception as exc:  # pypdf can raise different backend errors.
            # Codex说明(自动生成)： 抛出 PdfReadError(f'PDF 已加密，且无法用空密码打开: {path}')，明确提示输入、状态或处理流程无法继续。
            raise PdfReadError(f"PDF 已加密，且无法用空密码打开: {path}") from exc
        # Codex说明(自动生成)： 检查条件 not decrypt_result，根据结果选择后续执行路径。
        if not decrypt_result:
            # Codex说明(自动生成)： 抛出 PdfReadError(f'PDF 已加密，请先另存为未加密版本: {path}')，明确提示输入、状态或处理流程无法继续。
            raise PdfReadError(f"PDF 已加密，请先另存为未加密版本: {path}")
        # Codex说明(自动生成)： 调用 warnings.append 更新列表或集合，把当前步骤产生的数据加入结果。
        warnings.append(f"{path.name}: PDF 已加密，已尝试使用空密码读取。")

    # Codex说明(自动生成)： 声明并保存 pages，同时保留类型信息方便维护和静态检查。
    pages: list[PageText] = []
    # Codex说明(自动生成)： 声明并保存 empty_pages，同时保留类型信息方便维护和静态检查。
    empty_pages: list[int] = []
    # Codex说明(自动生成)： 遍历 enumerate(reader.pages, start=1) 中的 (index, page)，逐项执行循环体逻辑。
    for index, page in enumerate(reader.pages, start=1):
        # Codex说明(自动生成)： 开始执行可能失败的代码块，并把异常、收尾或兜底逻辑交给后续分支处理。
        try:
            # Codex说明(自动生成)： 计算并保存 text，供后续语句继续读取或更新。
            text = page.extract_text() or ""
        # Codex说明(自动生成)： 捕获 Exception，执行对应的恢复、记录或重新报错逻辑。
        except Exception as exc:  # Keep the run useful even if one page is odd.
            # Codex说明(自动生成)： 计算并保存 text，供后续语句继续读取或更新。
            text = ""
            # Codex说明(自动生成)： 调用 warnings.append 更新列表或集合，把当前步骤产生的数据加入结果。
            warnings.append(f"{path.name}: 第 {index} 页文本抽取失败: {exc}")
        # Codex说明(自动生成)： 检查条件 not text.strip()，根据结果选择后续执行路径。
        if not text.strip():
            # Codex说明(自动生成)： 调用 empty_pages.append 更新列表或集合，把当前步骤产生的数据加入结果。
            empty_pages.append(index)
        # Codex说明(自动生成)： 调用 pages.append 更新列表或集合，把当前步骤产生的数据加入结果。
        pages.append(PageText(page_number=index, text=text))

    # Codex说明(自动生成)： 计算并保存 extracted_chars，供后续语句继续读取或更新。
    extracted_chars = sum(len(page.text.strip()) for page in pages)
    # Codex说明(自动生成)： 检查条件 empty_pages，根据结果选择后续执行路径。
    if empty_pages:
        # Codex说明(自动生成)： 计算并保存 preview，供后续语句继续读取或更新。
        preview = ", ".join(str(page) for page in empty_pages[:12])
        # Codex说明(自动生成)： 计算并保存 suffix，供后续语句继续读取或更新。
        suffix = "..." if len(empty_pages) > 12 else ""
        # Codex说明(自动生成)： 调用 warnings.append 更新列表或集合，把当前步骤产生的数据加入结果。
        warnings.append(
            f"{path.name}: {len(empty_pages)} 页没有抽取到文字"
            f"（页码: {preview}{suffix}）。如果原文是扫描件，请先 OCR。"
        )
    # Codex说明(自动生成)： 检查条件 extracted_chars < 100，根据结果选择后续执行路径。
    if extracted_chars < 100:
        # Codex说明(自动生成)： 调用 warnings.append 更新列表或集合，把当前步骤产生的数据加入结果。
        warnings.append(
            f"{path.name}: 总共只抽取到 {extracted_chars} 个字符，"
            "结果可能不可靠；请确认 PDF 不是图片扫描版。"
        )

    # Codex说明(自动生成)： 返回 ExtractionResult(pdf_path=path, pages=pages, warnings=w...，让调用方取得本函数的处理结果。
    return ExtractionResult(pdf_path=path, pages=pages, warnings=warnings)
