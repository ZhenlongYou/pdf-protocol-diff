"""把用户直接启动的脚本切换到项目本地虚拟环境。"""

from __future__ import annotations  # 允许在旧运行时安全使用现代类型注解。

import os  # 用于判断 Windows 与 macOS/Linux 的虚拟环境脚本目录差异。
import sys  # 用于读取当前解释器前缀，并在需要时替换当前进程。
from pathlib import Path  # 用于可靠拼接和解析跨平台路径。


BOOTSTRAP_ATTEMPT_ENV = "PROTOCOL_PDF_DIFF_VENV_BOOTSTRAP_ATTEMPTED"  # 标记本进程已经尝试过一次 .venv 重启。


def project_venv_root(project_root: Path) -> Path:
    """返回项目约定使用的本地虚拟环境目录。"""

    return project_root / ".venv"  # 所有依赖都应安装在项目根目录下的 .venv。


def project_venv_python(project_root: Path) -> Path:
    """返回项目本地虚拟环境里的 Python 可执行文件路径。"""

    venv_root = project_venv_root(project_root)  # 先定位 .venv 根目录，后续按平台拼接解释器路径。
    if os.name == "nt":  # Windows 虚拟环境把 python.exe 放在 Scripts 目录。
        return venv_root / "Scripts" / "python.exe"  # 返回 Windows 下可直接执行的 Python。
    return venv_root / "bin" / "python"  # macOS/Linux 虚拟环境把 python 放在 bin 目录。


def should_reexec_into_project_venv(
    project_root: Path,
    current_prefix: str | Path | None = None,
) -> bool:
    """判断当前进程是否应该重启到项目 .venv。"""

    if getattr(sys, "frozen", False):  # PyInstaller 打包后没有源码旁边的 .venv，不能再重启。
        return False  # 冻结应用继续使用打包内置解释器和依赖。
    venv_root = project_venv_root(project_root).resolve()  # 解析真实 .venv 路径，用于和 sys.prefix 比较。
    if os.environ.get(BOOTSTRAP_ATTEMPT_ENV) == str(venv_root):  # 如果上一轮已经尝试进入同一个 .venv。
        return False  # 停止再次 exec，避免损坏的 .venv 造成无限重启。
    venv_python = project_venv_python(project_root)  # 找到项目 .venv 的 Python 可执行文件。
    if not venv_python.exists():  # 如果用户还没创建 .venv，就保留当前解释器并让依赖错误直说。
        return False  # 不伪造环境，也不静默安装到全局 Python。
    active_prefix = Path(current_prefix or sys.prefix).resolve()  # 使用 sys.prefix 判断当前虚拟环境归属。
    return active_prefix != venv_root  # 只要当前前缀不是项目 .venv，就需要重启。


def reexec_into_project_venv(project_root: Path, script_path: Path) -> None:
    """在用户直接运行入口脚本时，用项目 .venv 的 Python 替换当前进程。"""

    if not should_reexec_into_project_venv(project_root):  # 已在 .venv 或无法安全切换时直接继续。
        return  # 调用方随后按正常启动流程导入 GUI 或命令行依赖。
    venv_python = project_venv_python(project_root)  # 取得目标解释器，保证依赖从项目环境加载。
    os.environ[BOOTSTRAP_ATTEMPT_ENV] = str(project_venv_root(project_root).resolve())  # 标记这次重启目标，防止坏环境循环。
    argv = [str(venv_python), str(script_path), *sys.argv[1:]]  # 保留用户原始命令行参数。
    os.execv(str(venv_python), argv)  # 用 .venv Python 原地替换进程，避免留下双进程 GUI。
