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

from protocol_pdf_diff.desktop_gui import main, run_smoke_test  # noqa: E402


if __name__ == "__main__":
    if "--smoke-test" in sys.argv:
        run_smoke_test()
        raise SystemExit(0)
    raise SystemExit(main())
