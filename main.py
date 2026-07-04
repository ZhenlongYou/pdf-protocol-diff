"""Run protocol PDF comparison from PyCharm or the command line.

Edit the user-parameter block below for normal daily use, then press Run in
PyCharm or execute ``python3 main.py`` in this directory. Command-line flags are
also available for batch usage and override the editable defaults.
"""

# Codex说明(自动生成)： 从 __future__ 导入 annotations，启用较新的类型标注行为，减少运行期导入或前向引用问题。
from __future__ import annotations

# Codex说明(自动生成)： 导入 argparse，解析命令行参数，支持用户从终端覆盖默认配置。
import argparse
# Codex说明(自动生成)： 导入 sys，访问解释器路径、退出码和标准错误输出。
import sys
# Codex说明(自动生成)： 从 pathlib 导入 Path，用 Path 对象处理跨平台文件路径。
from pathlib import Path


# =============================================================================
# User parameters - edit these values before pressing Run in PyCharm.
# =============================================================================

# Old protocol PDF. Leave blank for the built-in demo run.
# Codex说明(自动生成)： 计算并保存 OLD_PDF_PATH，供后续语句继续读取或更新。
OLD_PDF_PATH = ""

# New protocol PDF. Leave blank for the built-in demo run.
# Codex说明(自动生成)： 计算并保存 NEW_PDF_PATH，供后续语句继续读取或更新。
NEW_PDF_PATH = ""

# Folder where timestamped report folders will be written.
# Codex说明(自动生成)： 计算并保存 OUTPUT_DIR，供后续语句继续读取或更新。
OUTPUT_DIR = "results"

# Minimum similarity for treating a renamed/renumbered section as the same
# logical section. Lower values match more aggressively; higher values report
# more sections as added/deleted.
# Codex说明(自动生成)： 计算并保存 MIN_SECTION_MATCH_SIMILARITY，供后续语句继续读取或更新。
MIN_SECTION_MATCH_SIMILARITY = 0.72

# Similarity at or above this value is considered unchanged and omitted from the
# default report. Lower this if you want tiny punctuation changes to show up.
# Codex说明(自动生成)： 计算并保存 UNCHANGED_SIMILARITY，供后续语句继续读取或更新。
UNCHANGED_SIMILARITY = 0.985

# Maximum snippets shown for each changed section. The full PDF is not copied
# into the report, so this keeps reports readable and copyright-safe.
# Codex说明(自动生成)： 计算并保存 MAX_SNIPPETS_PER_SECTION，供后续语句继续读取或更新。
MAX_SNIPPETS_PER_SECTION = 8

# When both paths above are blank, generate tiny demo PDFs so the no-argument run
# path always demonstrates the workflow. If either path is explicitly filled but
# invalid, the script fails instead of silently comparing demo files.
# Codex说明(自动生成)： 计算并保存 RUN_DEMO_IF_INPUTS_MISSING，供后续语句继续读取或更新。
RUN_DEMO_IF_INPUTS_MISSING = True

# Set True only when you want unchanged sections listed in the report too.
# Codex说明(自动生成)： 计算并保存 INCLUDE_UNCHANGED_SECTIONS，供后续语句继续读取或更新。
INCLUDE_UNCHANGED_SECTIONS = False

# =============================================================================


# Codex说明(自动生成)： 计算并保存 PROJECT_ROOT，供后续语句继续读取或更新。
PROJECT_ROOT = Path(__file__).resolve().parent
# Codex说明(自动生成)： 计算并保存 SRC_DIR，供后续语句继续读取或更新。
SRC_DIR = PROJECT_ROOT / "src"
# Codex说明(自动生成)： 检查条件 str(SRC_DIR) not in sys.path，根据结果选择后续执行路径。
if str(SRC_DIR) not in sys.path:
    # Codex说明(自动生成)： 调用 sys.path.insert 更新列表或集合，把当前步骤产生的数据加入结果。
    sys.path.insert(0, str(SRC_DIR))

# Codex说明(自动生成)： 从 protocol_pdf_diff.compare 导入 run_diff，提供本文件后续流程需要的库能力。
from protocol_pdf_diff.compare import run_diff  # noqa: E402
# Codex说明(自动生成)： 从 protocol_pdf_diff.models 导入 DiffOptions，提供本文件后续流程需要的库能力。
from protocol_pdf_diff.models import DiffOptions  # noqa: E402
# Codex说明(自动生成)： 从 protocol_pdf_diff.pdf_extract 导入 MissingDependencyError, PdfReadError，提供本文件后续流程需要的库能力。
from protocol_pdf_diff.pdf_extract import MissingDependencyError, PdfReadError  # noqa: E402
# Codex说明(自动生成)： 从 protocol_pdf_diff.reporting 导入 write_reports，提供本文件后续流程需要的库能力。
from protocol_pdf_diff.reporting import write_reports  # noqa: E402
# Codex说明(自动生成)： 从 protocol_pdf_diff.sample_data 导入 write_demo_pdfs，提供本文件后续流程需要的库能力。
from protocol_pdf_diff.sample_data import write_demo_pdfs  # noqa: E402


# Codex说明(自动生成)： 定义函数 parse_args，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def parse_args() -> argparse.Namespace:
    """Parse optional CLI overrides for automated runs."""

    # Codex说明(自动生成)： 计算并保存 parser，供后续语句继续读取或更新。
    parser = argparse.ArgumentParser(
        description="Compare old/new protocol PDFs and report chapter/section differences."
    )
    # Codex说明(自动生成)： 调用 parser.add_argument 注册命令行参数，让用户可以从终端配置运行选项。
    parser.add_argument("--old-pdf", default=OLD_PDF_PATH, help="旧协议 PDF 路径")
    # Codex说明(自动生成)： 调用 parser.add_argument 注册命令行参数，让用户可以从终端配置运行选项。
    parser.add_argument("--new-pdf", default=NEW_PDF_PATH, help="新协议 PDF 路径")
    # Codex说明(自动生成)： 调用 parser.add_argument 注册命令行参数，让用户可以从终端配置运行选项。
    parser.add_argument("--output-dir", default=OUTPUT_DIR, help="报告输出目录")
    # Codex说明(自动生成)： 调用 parser.add_argument 注册命令行参数，让用户可以从终端配置运行选项。
    parser.add_argument(
        "--min-section-match-similarity",
        type=float,
        default=MIN_SECTION_MATCH_SIMILARITY,
        help="章节匹配阈值，默认来自 main.py 用户参数区",
    )
    # Codex说明(自动生成)： 调用 parser.add_argument 注册命令行参数，让用户可以从终端配置运行选项。
    parser.add_argument(
        "--unchanged-similarity",
        type=float,
        default=UNCHANGED_SIMILARITY,
        help="未变化判定阈值，默认来自 main.py 用户参数区",
    )
    # Codex说明(自动生成)： 调用 parser.add_argument 注册命令行参数，让用户可以从终端配置运行选项。
    parser.add_argument(
        "--max-snippets",
        type=int,
        default=MAX_SNIPPETS_PER_SECTION,
        help="每个章节最多展示的差异片段数",
    )
    # Codex说明(自动生成)： 调用 parser.add_argument 注册命令行参数，让用户可以从终端配置运行选项。
    parser.add_argument(
        "--include-unchanged",
        action="store_true",
        default=INCLUDE_UNCHANGED_SECTIONS,
        help="在报告中也列出未变化章节",
    )
    # Codex说明(自动生成)： 调用 parser.add_argument 注册命令行参数，让用户可以从终端配置运行选项。
    parser.add_argument(
        "--demo",
        action="store_true",
        help="忽略 PDF 参数，生成并比较内置示例 PDF",
    )
    # Codex说明(自动生成)： 返回 parser.parse_args()，让调用方取得本函数的处理结果。
    return parser.parse_args()


# Codex说明(自动生成)： 定义函数 resolve_inputs，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def resolve_inputs(args: argparse.Namespace) -> tuple[Path, Path]:
    """Resolve user inputs, optionally creating demo PDFs.

    A missing path is not silently ignored unless demo mode is enabled. This
    avoids accidentally comparing stale files after a path typo.
    """

    # Codex说明(自动生成)： 检查条件 args.demo，根据结果选择后续执行路径。
    if args.demo:
        # Codex说明(自动生成)： 返回 write_demo_pdfs(PROJECT_ROOT / 'work' / 'demo_inputs')，让调用方取得本函数的处理结果。
        return write_demo_pdfs(PROJECT_ROOT / "work" / "demo_inputs")

    # Codex说明(自动生成)： 检查条件 not args.old_pdf and (not args.new_pdf) and RUN_DEMO_IF...，根据结果选择后续执行路径。
    if not args.old_pdf and not args.new_pdf and RUN_DEMO_IF_INPUTS_MISSING:
        # Codex说明(自动生成)： 输出面向用户的运行信息，帮助确认当前脚本进度或结果路径。
        print("未配置有效的新旧 PDF 路径，正在运行内置 demo。")
        # Codex说明(自动生成)： 输出面向用户的运行信息，帮助确认当前脚本进度或结果路径。
        print("实际使用时，请编辑 main.py 顶部的 OLD_PDF_PATH 和 NEW_PDF_PATH。")
        # Codex说明(自动生成)： 返回 write_demo_pdfs(PROJECT_ROOT / 'work' / 'demo_inputs')，让调用方取得本函数的处理结果。
        return write_demo_pdfs(PROJECT_ROOT / "work" / "demo_inputs")

    # Codex说明(自动生成)： 计算并保存 old_path，供后续语句继续读取或更新。
    old_path = Path(args.old_pdf).expanduser() if args.old_pdf else Path()
    # Codex说明(自动生成)： 计算并保存 new_path，供后续语句继续读取或更新。
    new_path = Path(args.new_pdf).expanduser() if args.new_pdf else Path()

    # Codex说明(自动生成)： 检查条件 not args.old_pdf or not old_path.exists()，根据结果选择后续执行路径。
    if not args.old_pdf or not old_path.exists():
        # Codex说明(自动生成)： 抛出 FileNotFoundError(f'旧协议 PDF 路径无效: {args.old_pdf!r}')，明确提示输入、状态或处理流程无法继续。
        raise FileNotFoundError(f"旧协议 PDF 路径无效: {args.old_pdf!r}")
    # Codex说明(自动生成)： 检查条件 not args.new_pdf or not new_path.exists()，根据结果选择后续执行路径。
    if not args.new_pdf or not new_path.exists():
        # Codex说明(自动生成)： 抛出 FileNotFoundError(f'新协议 PDF 路径无效: {args.new_pdf!r}')，明确提示输入、状态或处理流程无法继续。
        raise FileNotFoundError(f"新协议 PDF 路径无效: {args.new_pdf!r}")
    # Codex说明(自动生成)： 返回 (old_path.resolve(), new_path.resolve())，让调用方取得本函数的处理结果。
    return old_path.resolve(), new_path.resolve()


# Codex说明(自动生成)： 定义函数 main，把一段可复用的业务步骤、计算过程或入口逻辑封装起来。
def main() -> int:
    """Program entry point used by both PyCharm and command-line runs."""

    # Codex说明(自动生成)： 计算并保存 args，供后续语句继续读取或更新。
    args = parse_args()
    # Codex说明(自动生成)： 计算并保存 options，供后续语句继续读取或更新。
    options = DiffOptions(
        min_section_match_similarity=args.min_section_match_similarity,
        unchanged_similarity=args.unchanged_similarity,
        max_snippets_per_section=args.max_snippets,
        include_unchanged_sections=args.include_unchanged,
    )

    # Codex说明(自动生成)： 开始执行可能失败的代码块，并把异常、收尾或兜底逻辑交给后续分支处理。
    try:
        # Codex说明(自动生成)： 计算并保存 (old_pdf, new_pdf)，供后续语句继续读取或更新。
        old_pdf, new_pdf = resolve_inputs(args)
        # Codex说明(自动生成)： 计算并保存 result，供后续语句继续读取或更新。
        result = run_diff(old_pdf, new_pdf, options)
        # Codex说明(自动生成)： 计算并保存 outputs，供后续语句继续读取或更新。
        outputs = write_reports(result, PROJECT_ROOT / args.output_dir, options)
    # Codex说明(自动生成)： 捕获 (FileNotFoundError, MissingDependencyError, PdfReadErro...，执行对应的恢复、记录或重新报错逻辑。
    except (FileNotFoundError, MissingDependencyError, PdfReadError, ValueError) as exc:
        # Codex说明(自动生成)： 输出面向用户的运行信息，帮助确认当前脚本进度或结果路径。
        print(f"运行失败: {exc}", file=sys.stderr)
        # Codex说明(自动生成)： 返回 2，让调用方取得本函数的处理结果。
        return 2

    # Codex说明(自动生成)： 输出面向用户的运行信息，帮助确认当前脚本进度或结果路径。
    print("比较完成。报告已生成:")
    # Codex说明(自动生成)： 输出面向用户的运行信息，帮助确认当前脚本进度或结果路径。
    print(f"- Markdown: {outputs['markdown']}")
    # Codex说明(自动生成)： 输出面向用户的运行信息，帮助确认当前脚本进度或结果路径。
    print(f"- TXT:      {outputs['text']}")
    # Codex说明(自动生成)： 输出面向用户的运行信息，帮助确认当前脚本进度或结果路径。
    print(f"- CSV:      {outputs['csv']}")
    # Codex说明(自动生成)： 输出面向用户的运行信息，帮助确认当前脚本进度或结果路径。
    print(f"- JSON:     {outputs['json']}")
    # Codex说明(自动生成)： 检查条件 result.warnings，根据结果选择后续执行路径。
    if result.warnings:
        # Codex说明(自动生成)： 输出面向用户的运行信息，帮助确认当前脚本进度或结果路径。
        print("\n注意: 报告中包含 PDF 抽取警告，请先查看。")
    # Codex说明(自动生成)： 返回 0，让调用方取得本函数的处理结果。
    return 0


# Codex说明(自动生成)： 检查条件 __name__ == '__main__'，根据结果选择后续执行路径。
if __name__ == "__main__":
    # Codex说明(自动生成)： 结束当前命令行脚本，并把退出码交给操作系统；非零退出码表示运行失败。
    raise SystemExit(main())
