"""Build the desktop application with PyInstaller.

PyInstaller cannot cross-compile between macOS and Windows. Run this script on
the target operating system: on macOS it creates ``dist/ProtocolPdfDiff.app``;
on Windows it creates ``dist/ProtocolPdfDiff.exe``. The app bundles Python,
Tkinter, pdfplumber, table screenshot dependencies, and the project source so
end users do not need Python.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# 打包脚本也必须先进入项目 .venv，避免 PyInstaller 误用 Anaconda/base 的 GUI 栈。
from protocol_pdf_diff.venv_bootstrap import reexec_into_project_venv  # noqa: E402


if __name__ == "__main__":  # 只有直接执行打包脚本时才替换解释器，测试导入不触发 exec。
    reexec_into_project_venv(PROJECT_ROOT, Path(__file__).resolve())  # 保留原始打包参数并切到 .venv。

APP_NAME = "ProtocolPdfDiff"


def parse_args() -> argparse.Namespace:
    """Parse build options used by both macOS and Windows scripts."""

    parser = argparse.ArgumentParser(description="Build the desktop PDF diff app.")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove old build/dist artifacts before packaging.",
    )
    parser.add_argument(
        "--console",
        action="store_true",
        help="Keep a console window for troubleshooting instead of a GUI-only app.",
    )
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="Build one executable file instead of the default app/onedir layout.",
    )
    return parser.parse_args()


def main() -> int:
    """Run PyInstaller with platform-appropriate defaults."""

    args = parse_args()
    if args.clean:
        for name in ("build", "dist"):
            shutil.rmtree(PROJECT_ROOT / name, ignore_errors=True)
        spec = PROJECT_ROOT / f"{APP_NAME}.spec"
        if spec.exists():
            spec.unlink()

    try:
        import PyInstaller.__main__
    except ModuleNotFoundError:
        print(
            "缺少 PyInstaller。请先运行: python -m pip install -r requirements-build.txt",
            file=sys.stderr,
        )
        return 2
    missing = _missing_runtime_modules()
    if missing:
        print(
            "缺少运行依赖，不能打包不完整应用: "
            + ", ".join(missing)
            + "。请先运行: python -m pip install -r requirements-build.txt",
            file=sys.stderr,
        )
        return 2
    if shutil.which("tesseract") is None:
        print(
            "警告: 未发现 tesseract 可执行文件；打包应用仍会生成表格截图和行级表格差异，"
            "但不会执行表格 OCR。需要 OCR 时请在目标系统安装 Tesseract。",
            file=sys.stderr,
        )

    command = [
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--paths",
        str(PROJECT_ROOT / "src"),
    ]
    if not args.console:
        command.append("--windowed")
    if args.onefile:
        command.append("--onefile")
    if sys.platform == "darwin":
        command.extend(
            [
                "--osx-bundle-identifier",
                "com.rinys.protocolpdfdiff",
            ]
        )

    command.append(str(PROJECT_ROOT / "gui_app.py"))
    PyInstaller.__main__.run(command)

    artifact = expected_artifact(args.onefile)
    print(f"Build complete for {platform.system()}: {artifact}")
    smoke_test_artifact(artifact, args.onefile)
    return 0


def expected_artifact(onefile: bool) -> Path:
    """Return the primary artifact path produced on the current platform."""

    if sys.platform == "darwin" and not onefile:
        return PROJECT_ROOT / "dist" / f"{APP_NAME}.app"
    suffix = ".exe" if sys.platform.startswith("win") else ""
    return PROJECT_ROOT / "dist" / f"{APP_NAME}{suffix}"


def _missing_runtime_modules() -> list[str]:
    """Return required runtime modules that are missing before packaging."""

    required = ["pdfplumber", "pypdfium2", "PIL", "cv2", "pytesseract"]  # 表格截图和识别能力依赖这些模块。
    missing: list[str] = []  # 收集缺失模块，构建入口一次性提示。
    for module_name in required:
        try:
            __import__(module_name)
        except ModuleNotFoundError:
            missing.append(module_name)
    return missing


def smoke_test_artifact(artifact: Path, onefile: bool) -> None:
    """Run a non-interactive startup check for command-style artifacts.

    macOS ``.app`` bundles are checked through the executable inside the bundle.
    Windows and onefile artifacts support passing ``--smoke-test`` directly to
    the executable.
    """

    if sys.platform == "darwin" and artifact.suffix == ".app" and not onefile:
        # macOS 的 .app 是目录，真正可执行文件在 Contents/MacOS 下面。
        executable = artifact / "Contents" / "MacOS" / APP_NAME
        if not executable.exists():
            # 如果 bundle 内没有可执行文件，说明打包产物不完整，必须立刻失败。
            raise FileNotFoundError(f"Expected app executable was not created: {executable}")
        # 直接调用 bundle 内可执行文件，验证 GUI 关键控件在冻结环境里也存在。
        subprocess.run([str(executable), "--smoke-test"], check=True)
        # macOS bundle 已经完成专门检查，不再走普通文件路径。
        return
    if not artifact.exists():
        raise FileNotFoundError(f"Expected build artifact was not created: {artifact}")
    subprocess.run([str(artifact), "--smoke-test"], check=True)


if __name__ == "__main__":
    raise SystemExit(main())
