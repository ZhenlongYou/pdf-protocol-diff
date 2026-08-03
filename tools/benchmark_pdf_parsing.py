#!/usr/bin/env python3
"""运行 extraction-only PDF 解析基准并输出唯一的 summary JSON。"""

from __future__ import annotations

import argparse  # 提供 manifest/controlled 二选一和显式文件选项。
import json  # stdout 与可选输出文件共享同一 JSON 渲染结果。
from pathlib import Path  # CLI 路径参数保持跨平台且易于传给公开 runner。
import sys  # 在直接运行 tools 脚本时引入本项目 src，并返回进程退出码。


# tools 目录不在 src package 内，直接执行时需显式暴露本地源码。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    # 只把当前项目源码放在最高优先级，避免误导入全局旧版本。
    sys.path.insert(0, str(SRC_DIR))

from protocol_pdf_diff.parsing_benchmark import (  # noqa: E402
    run_controlled_parsing_benchmark,
    run_parsing_benchmark,
)


def main(argv: list[str] | None = None) -> int:
    """解析 CLI 参数、打印 summary JSON，并返回机器可用退出码。

    Args:
        argv: 可选参数列表；``None`` 时由 argparse 读取进程命令行。

    Returns:
        summary 为 fail 时返回 1，pass 或 skip 返回 0；参数冲突由 argparse
        返回 2。

    Privacy:
        stdout 只打印公开 runner 的受限 summary，不打印全文、resolved
        corpus root、hash 或报告路径。

    Side effects:
        读取 manifest/PDF；``--controlled`` 会创建后清理临时 fixtures；
        ``--output-json`` 会创建父目录并覆盖指定 JSON 文件。
    """

    # 程序说明保持简短，详细 schema 由示例 manifest 和 validator 错误表达。
    parser = argparse.ArgumentParser(description=__doc__)
    # positional manifest 与 --controlled 共享互斥组，避免模糊执行来源。
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        help="parsing benchmark manifest JSON",
    )
    mode.add_argument(
        "--controlled",
        action="store_true",
        help="generate temporary controlled fixtures and benchmark them",
    )
    # corpus root 仅影响 manifest 模式；保留为独立选项方便私有 corpus 部署。
    parser.add_argument("--corpus-root", type=Path)
    # 输出文件是 stdout JSON 的精确副本，便于 CI 留档。
    parser.add_argument("--output-json", type=Path)
    # argparse 自行处理帮助和参数错误；正常路径继续得到结构化 summary。
    args = parser.parse_args(argv)

    if args.controlled and args.corpus_root is not None:
        # 受控 fixtures 固定在受管临时目录；混入用户 corpus root 容易让调用者
        # 误以为该目录参与了验证，因此在执行前明确拒绝这个无效组合。
        parser.error("--corpus-root cannot be used with --controlled")

    if args.controlled:
        # controlled 入口内部生成/清理 fixtures，不接受持久 corpus 输入。
        summary = run_controlled_parsing_benchmark()
    else:
        # 互斥组保证此分支中 manifest 一定存在。
        summary = run_parsing_benchmark(args.manifest, corpus_root=args.corpus_root)

    # ensure_ascii=False 保留 anchor/失败文本的 Unicode 可读性。
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    # stdout 只调用一次 print，确保消费者读到单个完整 JSON document。
    print(rendered)
    if args.output_json is not None:
        # 按用户显式路径创建父目录，不在项目内生成默认输出。
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n", encoding="utf-8")
    # fail 是唯一非零业务状态；全 skip 属于环境性成功执行。
    return 1 if summary["status"] == "fail" else 0


if __name__ == "__main__":
    # 把 main 的整数状态交给 shell，不额外打印日志污染 stdout JSON。
    raise SystemExit(main())
