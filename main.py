"""Run protocol PDF comparison from PyCharm or the command line.

Edit the user-parameter block below for normal daily use, then press Run in
PyCharm or execute ``python3 main.py`` in this directory. Command-line flags are
also available for batch usage and override the editable defaults.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


# =============================================================================
# User parameters - edit these values before pressing Run in PyCharm.
# =============================================================================

# Old protocol PDF. Leave blank for the built-in demo run.
OLD_PDF_PATH = ""

# New protocol PDF. Leave blank for the built-in demo run.
NEW_PDF_PATH = ""

# Optional old PDF page window. Use one-based PDF-reader page numbers; the end
# page is included. Leave as None to start from page 1 or continue to the end.
OLD_START_PAGE = None
OLD_END_PAGE = None

# Optional new PDF page window. These values are independent from the old PDF
# window, which is useful when added content shifts the page numbers.
NEW_START_PAGE = None
NEW_END_PAGE = None

# Folder where timestamped report folders will be written.
OUTPUT_DIR = "results"

# Minimum similarity for treating a renamed/renumbered section as the same
# logical section. Lower values match more aggressively; higher values report
# more sections as added/deleted.
MIN_SECTION_MATCH_SIMILARITY = 0.72

# Maximum visible diff snippets per changed section. This does not limit
# section matching or comparison; it only keeps the generated report readable.
MAX_SNIPPETS_PER_SECTION = 20

# Optional Tesseract language expression for scan-like pages. Examples:
# "eng", "chi_sim", or "chi_sim+eng". Leave as None for Tesseract's default.
OCR_LANGUAGE = None

# Optional layout parser policy. ``native`` is the fast default and never
# imports Docling. Set ``auto`` only when testing complex multi-column pages;
# ``docling`` requests the same guarded repair and reports a clear install error
# if its optional dependency is not present.
LAYOUT_BACKEND = "native"

# Keep the source-pixel watchdog enabled for normal reviews.  It only compares
# pages whose extracted text is identical and reports unexplained graphics as
# manual-review evidence; it never turns pixels into invented technical text.
VISUAL_WATCHDOG = True

# When both paths above are blank, generate multi-page demo PDFs so the
# no-argument run demonstrates page drift, headers/footers, and section changes.
# If either path is explicitly filled but invalid, the script fails instead of
# silently comparing demo files.
RUN_DEMO_IF_INPUTS_MISSING = True

# Set True only when you want unchanged sections listed in the report too.
INCLUDE_UNCHANGED_SECTIONS = False

# =============================================================================


_LEGACY_UNCHANGED_SIMILARITY = 0.985  # 仅供旧 CLI 启动脚本解析兼容，不是用户可调判定参数。


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 导入只依赖标准库的启动辅助；它必须早于 PDF 抽取和比较模块导入。
from protocol_pdf_diff.venv_bootstrap import reexec_into_project_venv  # noqa: E402


if __name__ == "__main__":  # PyCharm 直接运行 main.py 时先切到项目 .venv，避免缺少 pdfplumber。
    reexec_into_project_venv(PROJECT_ROOT, Path(__file__).resolve())  # 使用 sys.prefix 判断环境，避开 macOS 软链接误判。

from protocol_pdf_diff.compare import run_diff  # noqa: E402
from protocol_pdf_diff.models import DiffOptions  # noqa: E402
from protocol_pdf_diff.pdf_extract import MissingDependencyError, PdfReadError  # noqa: E402
from protocol_pdf_diff.quality import ReliabilityState  # noqa: E402
from protocol_pdf_diff.reporting import write_reports  # noqa: E402
from protocol_pdf_diff.sample_data import write_demo_pdfs  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse optional CLI overrides for automated runs."""

    parser = argparse.ArgumentParser(
        description="Compare old/new protocol PDFs and report chapter/section differences."
    )
    parser.add_argument("--old-pdf", default=OLD_PDF_PATH, help="旧协议 PDF 路径")
    parser.add_argument("--new-pdf", default=NEW_PDF_PATH, help="新协议 PDF 路径")
    parser.add_argument(
        "--old-start-page",
        type=int,
        default=OLD_START_PAGE,
        help="旧协议起始页，1-based 且包含该页；不填则从第一页开始",
    )
    parser.add_argument(
        "--old-end-page",
        type=int,
        default=OLD_END_PAGE,
        help="旧协议终止页，1-based 且包含该页；不填则到最后一页",
    )
    parser.add_argument(
        "--new-start-page",
        type=int,
        default=NEW_START_PAGE,
        help="新协议起始页，1-based 且包含该页；不填则从第一页开始",
    )
    parser.add_argument(
        "--new-end-page",
        type=int,
        default=NEW_END_PAGE,
        help="新协议终止页，1-based 且包含该页；不填则到最后一页",
    )
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="报告输出目录")
    parser.add_argument(
        "--min-section-match-similarity",
        type=float,
        default=MIN_SECTION_MATCH_SIMILARITY,
        help="章节匹配阈值，默认来自 main.py 用户参数区",
    )
    parser.add_argument(
        "--unchanged-similarity",
        type=float,
        default=_LEGACY_UNCHANGED_SIMILARITY,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-snippets",
        type=int,
        default=MAX_SNIPPETS_PER_SECTION,
        help="每个章节最多展示的差异片段数；不限制完整章节匹配和比较",
    )
    parser.add_argument(
        "--include-unchanged",
        action="store_true",
        default=INCLUDE_UNCHANGED_SECTIONS,
        help="在报告中也列出未变化章节",
    )
    parser.add_argument(
        "--ocr-language",
        default=OCR_LANGUAGE,
        help="扫描页 OCR 语言，例如 eng 或 chi_sim+eng；需要对应 Tesseract 语言包",
    )
    parser.add_argument(
        "--layout-backend",
        choices=("native", "auto", "docling"),
        default=LAYOUT_BACKEND,
        help="版面解析策略：native（默认快速）、auto（仅复杂页）或 docling（需可选依赖）",
    )
    parser.add_argument(
        "--no-visual-watchdog",
        action="store_false",
        dest="visual_watchdog",
        default=VISUAL_WATCHDOG,
        help="关闭文字一致页的视觉漏检核对；仅建议用于性能排障",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="忽略 PDF 参数，生成并比较内置示例 PDF",
    )
    parser.add_argument(
        "--gui-smoke-test",
        action="store_true",
        help="构造真实桌面界面并检查跨平台字体、布局和关键控件后退出",
    )  # 与 TestRecord 的生产自检入口对齐，Windows CI 不需要模拟用户点击。
    return parser.parse_args()


def resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path]:
    """Resolve user inputs, optionally creating demo PDFs.

    A missing path is not silently ignored unless demo mode is enabled. This
    avoids accidentally comparing stale files after a path typo.
    """

    if args.demo:
        return write_demo_pdfs(PROJECT_ROOT / "work" / "demo_inputs")

    if not args.old_pdf and not args.new_pdf and RUN_DEMO_IF_INPUTS_MISSING:
        print("未配置有效的新旧 PDF 路径，正在运行内置多页 demo。")
        print("实际使用时，请编辑 main.py 顶部的 OLD_PDF_PATH 和 NEW_PDF_PATH。")
        return write_demo_pdfs(PROJECT_ROOT / "work" / "demo_inputs")

    old_path = Path(args.old_pdf).expanduser() if args.old_pdf else Path()
    new_path = Path(args.new_pdf).expanduser() if args.new_pdf else Path()

    if not args.old_pdf or not old_path.exists():
        raise FileNotFoundError(f"旧协议 PDF 路径无效: {args.old_pdf!r}")
    if not args.new_pdf or not new_path.exists():
        raise FileNotFoundError(f"新协议 PDF 路径无效: {args.new_pdf!r}")
    return old_path.resolve(), new_path.resolve()


def main() -> int:
    """Program entry point used by both PyCharm and command-line runs."""

    args = parse_args()
    if args.gui_smoke_test:
        from protocol_pdf_diff.desktop_gui import run_smoke_test  # 延迟导入确保先完成项目 .venv 切换。

        run_smoke_test()  # 真实创建字体、Canvas、滚动条和输入控件，不用无界面的假对象替代。
        print("GUI smoke test passed")  # 稳定标记供 Windows CI、PyCharm 和交付门禁判断成功。
        return 0  # 自检不需要 PDF 输入，也不得继续进入耗时比较流程。
    try:
        options = DiffOptions(
            min_section_match_similarity=args.min_section_match_similarity,
            unchanged_similarity=args.unchanged_similarity,
            max_snippets_per_section=args.max_snippets,
            include_unchanged_sections=args.include_unchanged,
            old_start_page=args.old_start_page,
            old_end_page=args.old_end_page,
            new_start_page=args.new_start_page,
            new_end_page=args.new_end_page,
            ocr_language=args.ocr_language,
            layout_backend=args.layout_backend,
            visual_watchdog=args.visual_watchdog,
        )  # 共享配置验证属于可预期的用户输入错误，必须由同一中文错误路径捕获。
        old_pdf, new_pdf = resolve_inputs(args)
        result = run_diff(old_pdf, new_pdf, options)
        outputs = write_reports(result, PROJECT_ROOT / args.output_dir, options)
    except (FileNotFoundError, MissingDependencyError, PdfReadError, ValueError) as exc:
        print(f"运行失败: {exc}", file=sys.stderr)
        return 2

    assessment = result.assessment
    if assessment is None:
        reliability_label = "无法判断"
        reliability_headline = "比较结果缺少可靠性评估数据"
    elif assessment.state is ReliabilityState.RELIABLE:
        reliability_label = "可靠"
        reliability_headline = assessment.headline
    elif assessment.state is ReliabilityState.DEGRADED:
        reliability_label = "需人工复核"
        reliability_headline = assessment.headline
    else:
        reliability_label = "无法判断"
        reliability_headline = assessment.headline

    print("处理完成。报告已生成:")
    print(f"- 识别可信度: {reliability_label}")
    print(f"- 结论: {reliability_headline}")
    print(f"- HTML:     {outputs['html']}")
    print(f"- Markdown: {outputs['markdown']}")
    print(f"- TXT:      {outputs['text']}")
    print(f"- 正文 CSV: {outputs['csv']}")
    print(f"- 表格 CSV: {outputs['table_csv']}")
    print(f"- JSON:     {outputs['json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
