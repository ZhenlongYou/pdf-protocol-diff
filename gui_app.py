"""Desktop GUI entry point for PyInstaller and direct local runs.

Run ``python3 gui_app.py`` during development, or package this file with
PyInstaller to produce a macOS app / Windows exe. The script bootstraps the
local ``src`` directory so it works before the package is installed.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 导入只依赖标准库的启动辅助；它必须早于 GUI/PDF 依赖导入。
from protocol_pdf_diff.venv_bootstrap import reexec_into_project_venv  # noqa: E402


if __name__ == "__main__":  # 只有用户直接启动 GUI 时才切换环境，避免测试导入时替换进程。
    reexec_into_project_venv(PROJECT_ROOT, Path(__file__).resolve())  # 先进入 .venv，再导入 WebView 和 PDF 依赖。

from protocol_pdf_diff.webview_gui import main, run_smoke_test  # noqa: E402


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        run_smoke_test(real_window=True)
        raise SystemExit(0)
    raise SystemExit(main())
