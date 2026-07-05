"""Build the desktop application with PyInstaller.

PyInstaller cannot cross-compile between macOS and Windows. Run this script on
the target operating system: on macOS it creates ``dist/ProtocolPdfDiff.app``;
on Windows it creates ``dist/ProtocolPdfDiff.exe``. The app bundles Python,
Tkinter, pypdf, and the project source so end users do not need Python.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
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


def smoke_test_artifact(artifact: Path, onefile: bool) -> None:
    """Run a non-interactive startup check for command-style artifacts.

    macOS ``.app`` bundles are smoke-tested separately because launching a GUI
    bundle from a shell is asynchronous. Windows and onefile artifacts support
    passing ``--smoke-test`` directly to the executable.
    """

    if sys.platform == "darwin" and artifact.suffix == ".app" and not onefile:
        return
    if not artifact.exists():
        raise FileNotFoundError(f"Expected build artifact was not created: {artifact}")
    subprocess.run([str(artifact), "--smoke-test"], check=True)


if __name__ == "__main__":
    raise SystemExit(main())
