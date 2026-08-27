"""Tkinter desktop interface for the protocol PDF diff workflow.

The command-line entry point remains useful for automation, but most reviewers
need a small desktop tool: choose two PDFs, optionally type page ranges, run the
comparison, then open the HTML report. This module keeps that GUI thin and
delegates all PDF and diff behavior to the same core pipeline that the tests
already exercise.
"""

from __future__ import annotations

import os
import queue
import math  # GUI 在启动后台任务前拒绝 NaN、Inf 和超出比例区间的阈值。
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable  # 为可注入的 Win32 DPI setter 提供明确调用契约，便于跨平台测试。
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
import tkinter.font as tkfont  # 读取当前 Tcl/Tk 实际可用字体，避免 Windows 回退不存在的 macOS 字体。
from tkinter import ttk

from .compare import run_diff
from .models import DiffOptions, DiffResult
from .pdf_extract import MissingDependencyError, PdfReadError
from .progress import ProgressEvent, notify_progress
from .quality import ReliabilityState
from .reporting import write_reports


PROJECT_ROOT = Path(__file__).resolve().parents[2]


# 以中性石墨为画布、逐级抬升深灰表面；冷蓝只服务于主操作、焦点和进度。
UI_THEME = {
    "canvas": "#111216",
    "surface": "#1A1C22",
    "surface_raised": "#20232B",
    "section_border": "#3B424F",
    "input": "#15171C",
    "border": "#657080",
    "border_strong": "#7B8494",
    "ink": "#F0F2F5",
    "secondary_ink": "#D4D8E0",
    "muted": "#A8AFBD",
    "accent": "#7D9EEC",
    "accent_active": "#6685D2",
    "focus_ring": "#C5D2FF",
    "accent_soft": "#28324A",
    "accent_ink": "#111827",
    "browse_button": "#3D5078",
    "browse_button_hover": "#465D8A",
    "browse_button_active": "#334361",
    "browse_button_border": "#8FA5D6",
    "button_hover": "#272A33",
    "button_active": "#303541",
    "disabled_surface": "#1A1C21",
    "disabled_ink": "#7D8490",
    "disabled_accent": "#4B5B7B",
    "disabled_accent_ink": "#D4DEFA",
}


def select_ui_font(
    available_families: set[str] | tuple[str, ...],
    *,
    default_family: str,
    platform_name: str | None = None,
) -> str:
    """Return the first installed native-looking Chinese UI font for one OS."""

    platform = platform_name or sys.platform  # 测试可传入目标平台，生产环境使用真实操作系统。
    available = set(available_families)  # 集合查询避免字体列表较长时重复线性扫描。
    if platform.startswith("win"):
        candidates = ("Microsoft YaHei UI", "Segoe UI", "Arial")  # Windows 优先保证中文和系统控件字形协调。
    elif platform == "darwin":
        candidates = ("PingFang SC", "SF Pro Text", "Helvetica Neue", "Arial")  # macOS 保留当前苹方视觉基线。
    else:
        candidates = (
            "Noto Sans CJK SC",
            "Noto Sans CJK",
            "WenQuanYi Micro Hei",
            "DejaVu Sans",
        )  # Linux 选择常见 CJK 字体，并保留通用无衬线回退。
    return next(
        (family for family in candidates if family in available),
        default_family,
    )  # 精简系统没有候选字体时使用 Tk 已解析的默认字体，不返回虚假字体名。


def preferred_ui_font(root: tk.Misc) -> str:
    """Resolve the concrete UI font available to the current Tk interpreter."""

    available = tkfont.families(root)  # 字体可用性由当前显示会话和 Tcl/Tk 解释器共同决定。
    default_family = str(
        tkfont.nametofont("TkDefaultFont", root=root).actual("family")
    )  # Tk 默认字体是候选缺失时最可信的最终回退。
    return select_ui_font(available, default_family=default_family)  # 统一走可测试的纯字体选择逻辑。


def responsive_window_size(
    screen_width: int,
    screen_height: int,
    *,
    target_width: int,
    target_height: int,
    floor_width: int,
    floor_height: int,
    margin: int = 80,
) -> tuple[int, int]:
    """Keep the initial window inside the logical screen without losing usability."""

    usable_width = max(
        min(floor_width, screen_width),
        screen_width - margin,
    )  # 正常屏幕保留系统边缘空间，极小屏幕则允许窗口降到实际宽度。
    usable_height = max(
        min(floor_height, screen_height),
        screen_height - margin,
    )  # 高 DPI 后的逻辑高度可能很小，不能坚持固定 700px 导致按钮落到屏幕外。
    return min(target_width, usable_width), min(
        target_height,
        usable_height,
    )  # 目标尺寸只在当前屏幕容得下时采用。


def configure_windows_dpi_awareness(
    *,
    platform_name: str | None = None,
    modern_setter: Callable[[int], object] | None = None,
) -> str:
    """Request Windows Per-Monitor V2 scaling before the first Tk window exists."""

    platform = platform_name or sys.platform  # 测试注入目标平台，生产使用当前解释器平台。
    if not platform.startswith("win"):
        return "not-applicable"  # macOS/Linux 不加载 Win32 DLL，继续使用各自 Tk 缩放机制。
    setter = modern_setter  # 注入点让 macOS CI 能验证 Windows 常量和调用时机。
    if setter is None:
        try:
            import ctypes  # 仅 Windows 启动路径需要加载系统 DLL，避免其它平台产生无意义依赖。

            user32 = ctypes.WinDLL("user32", use_last_error=True)  # 保留 Win32 last-error 供现场排错。
            setter = user32.SetProcessDpiAwarenessContext  # Windows 10 1703+ 支持 Per-Monitor V2。
            setter.argtypes = [ctypes.c_void_p]  # DPI awareness context 是 Win32 句柄值而不是普通整数。
            setter.restype = ctypes.c_bool  # BOOL 返回值转换为 Python 布尔语义。
        except (AttributeError, OSError):
            return "unsupported"  # 旧 Windows 没有现代 API 时由打包 manifest 的兼容字段接管。
    try:
        applied = bool(setter(-4))  # -4 是 DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2。
    except (OSError, TypeError, ValueError):
        return "unsupported"  # API 不可用时不阻断 GUI，仍允许系统或 manifest 处理缩放。
    if applied:
        return "per-monitor-v2"  # 成功必须发生在任何 Tk 根窗口创建之前。
    return "already-configured"  # manifest 或先前调用已设置时 Win32 会拒绝重复修改，属于可接受状态。


def create_tk_root() -> tk.Tk:
    """Create the first Tk root only after process DPI awareness is configured."""

    configure_windows_dpi_awareness()  # 微软要求在任何 HWND/Tk 窗口创建前设置进程 DPI awareness。
    return tk.Tk()  # 所有生产与 smoke-test 入口共用此工厂，避免某条路径遗漏调用时机。


def default_output_dir() -> Path:
    """Return a user-writable report folder outside the packaged app bundle."""

    # 把默认报告目录放在用户文档目录，避免打包后的 app 尝试写入只读 bundle。
    return Path.home() / "Documents" / "ProtocolPdfDiffReports"


@dataclass(frozen=True)
class DesktopRunConfig:
    """Validated settings collected from the desktop form before a run."""

    old_pdf: Path
    new_pdf: Path
    output_dir: Path
    options: DiffOptions


@dataclass(frozen=True)
class DesktopRunSuccess:
    """Result payload returned from the background comparison thread."""

    result: DiffResult
    outputs: dict[str, Path]


def parse_optional_page(value: str, label: str) -> int | None:
    """Parse an optional one-based page field from the GUI.

    Empty fields mean "use the default range edge." Non-empty values must be
    positive integers because the PDF extraction layer reports pages using the
    same one-based numbering that users see in a PDF reader.
    """

    stripped = value.strip()
    if not stripped:
        return None
    try:
        page = int(stripped)
    except ValueError as exc:
        raise ValueError(f"{label} 必须是正整数。") from exc
    if page < 1:
        raise ValueError(f"{label} 必须大于等于 1。")
    return page


def page_range_values(
    mode: str,
    start_value: str,
    end_value: str,
    label: str,
) -> tuple[int | None, int | None]:
    """Resolve a document page mode without discarding hidden range values."""

    if mode == "all":
        return None, None
    if mode != "range":
        raise ValueError(f"{label}页码模式无效。")
    start = parse_optional_page(start_value, f"{label}起始页")
    end = parse_optional_page(end_value, f"{label}终止页")
    if start is None or end is None:
        raise ValueError(f"{label}指定范围时必须填写起始页和终止页。")
    if start > end:
        raise ValueError(f"{label}起始页不能大于终止页。")
    return start, end


def parse_positive_float(value: str, label: str) -> float:
    """Parse a positive float from a compact tuning field."""

    try:
        number = float(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label} 必须是数字。") from exc
    if not math.isfinite(number) or not 0.0 < number <= 1.0:  # 匹配阈值是有限比例，不能接受 NaN、Inf 或大于 1。
        raise ValueError(f"{label} 必须大于 0；允许区间为 0 到 1（含 1），且必须是有限数字。")  # 同时保留既有提示关键词并明确上限与 NaN/Inf 限制。
    return number


def parse_positive_int(value: str, label: str) -> int:
    """Parse a positive integer from a compact tuning field."""

    try:
        number = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{label} 必须是整数。") from exc
    if number < 0:
        raise ValueError(f"{label} 不能小于 0。")
    return number


@dataclass(frozen=True)
class ReaderReportSummary:
    """Counts already filtered into the human-facing report."""

    body_changes: int
    modified: int
    added: int
    deleted: int
    table_changes: int
    visual_items: int


def _reported_reader_summary(outputs: dict[str, Path]) -> ReaderReportSummary | None:
    """Read the Markdown summary so desktop and report never show different totals."""

    markdown_path = outputs.get("markdown")
    if markdown_path is None:
        return None
    try:
        lines = markdown_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    values: dict[str, str] = {}
    in_summary = False
    for line in lines:
        if line.strip() == "## 汇总":
            in_summary = True
            continue
        if in_summary and line.startswith("## "):
            break
        if not in_summary:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 2:
            values[cells[0]] = cells[1]
    try:
        modified, added, deleted = (
            int(value.strip())
            for value in values["章节修改 / 新增 / 删除"].split("/")
        )
        return ReaderReportSummary(
            body_changes=int(values["核心技术变化"]),
            modified=modified,
            added=added,
            deleted=deleted,
            table_changes=int(values["变化表格"]),
            visual_items=int(values["视觉漏检核对项"]),
        )
    except (KeyError, ValueError):
        return None


class ProtocolDiffDesktopApp:
    """Desktop GUI coordinator for selecting PDFs and launching comparisons."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("协议 PDF 差异对比工具")
        self.design_window_size = (1120, 720)
        self.initial_window_size = responsive_window_size(
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
            target_width=self.design_window_size[0],
            target_height=self.design_window_size[1],
            floor_width=760,
            floor_height=520,
        )
        self.root.geometry(
            f"{self.initial_window_size[0]}x{self.initial_window_size[1]}"
        )  # 显式初始尺寸消除不同 Tk 平台按请求尺寸推导出的启动差异。
        self.minimum_window_size = (
            min(760, self.initial_window_size[0]),
            min(520, self.initial_window_size[1]),
        )  # 小屏允许降到真实可用尺寸，内部滚动区负责保证全部操作仍可到达。
        self.root.minsize(*self.minimum_window_size)  # 最小尺寸与当前逻辑屏幕绑定，不再固定逼出屏幕边界。
        self.ui_font = preferred_ui_font(self.root)  # 当前系统真实字体只解析一次，所有 Tk/ttk 控件共用同一结果。

        self.old_pdf_var = tk.StringVar()
        self.new_pdf_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(default_output_dir()))  # 默认输出到用户可写目录。
        self.old_start_var = tk.StringVar()
        self.old_end_var = tk.StringVar()
        self.new_start_var = tk.StringVar()
        self.new_end_var = tk.StringVar()
        self.old_page_mode_var = tk.StringVar(value="all")
        self.new_page_mode_var = tk.StringVar(value="all")
        self.min_similarity_var = tk.StringVar(value="0.72")
        self.max_snippets_var = tk.StringVar(value="20")
        self.include_unchanged_var = tk.BooleanVar(value=False)
        self.auto_open_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="空闲 · 请选择旧版和新版 PDF。")
        self.summary_var = tk.StringVar(value="尚未生成报告")
        self.report_path_var = tk.StringVar(value="")

        self._last_outputs: dict[str, Path] | None = None
        self._result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.page_entry_widgets: dict[str, tk.Entry] = {}  # 保存四个原生页码输入框，供 smoke test 检查真实输入能力。
        self.file_browse_buttons: list[ttk.Button] = []  # 保存三个“选择”按钮，供打包后自测确认按钮存在。
        self.input_widgets: list[tk.Widget] = []
        self._input_states: dict[tk.Widget, str] = {}
        self.advanced_expanded = False
        self.document_layout_mode = "side-by-side"
        self.is_running = False
        self._run_started_at: float | None = None
        self._current_progress_event: ProgressEvent | None = None
        self._elapsed_after_id: str | None = None

        self._configure_style()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_requested)

    def _configure_style(self) -> None:
        """Apply a precise, high-contrast visual system without moving the layout."""

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            # 固定控件渲染，避免不同系统主题改变输入框、边框和禁用态的层级。
            style.theme_use("clam")
        self.root.configure(background=UI_THEME["canvas"])
        self.root.option_add("*Font", (self.ui_font, 12))  # 覆盖原生 Tk 控件，避免页码框在 Windows 单独回退字体。
        style.configure(
            ".",
            font=(self.ui_font, 12),
            background=UI_THEME["canvas"],
            foreground=UI_THEME["secondary_ink"],
        )
        style.configure("TFrame", background=UI_THEME["canvas"])
        # 标题和状态标签有专属样式；其余标签随所在分区使用抬升表面，避免出现底色断层。
        style.configure("TLabel", background=UI_THEME["surface_raised"], foreground=UI_THEME["secondary_ink"])
        style.configure(
            "Title.TLabel",
            background=UI_THEME["canvas"],
            foreground=UI_THEME["ink"],
            font=(self.ui_font, 22, "bold"),
        )
        style.configure(
            "Status.TLabel",
            background=UI_THEME["canvas"],
            foreground=UI_THEME["muted"],
            font=(self.ui_font, 12),
        )
        style.configure(
            "SectionBody.TFrame",
            background=UI_THEME["surface_raised"],
        )
        style.configure(
            "SectionTitle.TLabel",
            background=UI_THEME["surface_raised"],
            foreground=UI_THEME["ink"],
            font=(self.ui_font, 12, "bold"),
        )
        style.configure(
            "TEntry",
            fieldbackground=UI_THEME["input"],
            foreground=UI_THEME["ink"],
            bordercolor=UI_THEME["border"],
            lightcolor=UI_THEME["border"],
            darkcolor=UI_THEME["border"],
            relief="flat",
            borderwidth=1,
            padding=(8, 6),
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", UI_THEME["accent"])],
            lightcolor=[("focus", UI_THEME["accent"])],
            fieldbackground=[("focus", UI_THEME["surface"])],
        )
        style.configure(
            "TButton",
            background=UI_THEME["surface_raised"],
            foreground=UI_THEME["secondary_ink"],
            bordercolor=UI_THEME["border_strong"],
            lightcolor=UI_THEME["border_strong"],
            darkcolor=UI_THEME["border_strong"],
            relief="flat",
            padding=(12, 7),
        )
        style.map(
            "TButton",
            background=[
                ("active", UI_THEME["button_hover"]),
                ("pressed", UI_THEME["button_active"]),
                ("disabled", UI_THEME["disabled_surface"]),
            ],
            foreground=[("disabled", UI_THEME["disabled_ink"])],
            bordercolor=[("focus", UI_THEME["accent"]), ("active", UI_THEME["border_strong"])],
        )
        # 文件选择是高频次级操作：使用独立的冷蓝填充，避免按钮融入卡片背景。
        style.configure(
            "Browse.TButton",
            background=UI_THEME["browse_button"],
            foreground=UI_THEME["ink"],
            bordercolor=UI_THEME["browse_button_border"],
            lightcolor=UI_THEME["browse_button_border"],
            darkcolor=UI_THEME["browse_button_border"],
            relief="flat",
            font=(self.ui_font, 12, "bold"),
            padding=(14, 7),
        )
        style.map(
            "Browse.TButton",
            background=[
                ("active", UI_THEME["browse_button_hover"]),
                ("pressed", UI_THEME["browse_button_active"]),
            ],
            bordercolor=[
                ("focus", UI_THEME["focus_ring"]),
                ("active", UI_THEME["browse_button_border"]),
            ],
            lightcolor=[("focus", UI_THEME["focus_ring"])],
            darkcolor=[("focus", UI_THEME["focus_ring"])],
        )
        style.configure(
            "Primary.TButton",
            background=UI_THEME["accent"],
            foreground=UI_THEME["accent_ink"],
            bordercolor=UI_THEME["accent"],
            lightcolor=UI_THEME["accent"],
            darkcolor=UI_THEME["accent"],
            relief="flat",
            font=(self.ui_font, 12, "bold"),
            padding=(16, 8),
        )
        style.map(
            "Primary.TButton",
            background=[
                ("active", UI_THEME["accent_active"]),
                ("pressed", UI_THEME["accent_active"]),
                ("disabled", UI_THEME["disabled_accent"]),
            ],
            foreground=[("disabled", UI_THEME["disabled_accent_ink"])],
            bordercolor=[("focus", UI_THEME["focus_ring"])],
            lightcolor=[("focus", UI_THEME["focus_ring"])],
            darkcolor=[("focus", UI_THEME["focus_ring"])],
        )
        style.configure(
            "TCheckbutton",
            background=UI_THEME["surface_raised"],
            foreground=UI_THEME["secondary_ink"],
            indicatorcolor=UI_THEME["input"],
            indicatormargin=(0, 0, 5, 0),
        )
        style.map(
            "TCheckbutton",
            foreground=[("focus", UI_THEME["focus_ring"]), ("disabled", UI_THEME["disabled_ink"])],
            background=[("focus", UI_THEME["accent_soft"])],
            indicatorcolor=[("selected", UI_THEME["accent"]), ("active", UI_THEME["accent_soft"])],
        )
        style.configure(
            "TRadiobutton",
            background=UI_THEME["surface_raised"],
            foreground=UI_THEME["secondary_ink"],
            indicatorcolor=UI_THEME["input"],
            indicatormargin=(0, 0, 5, 0),
        )
        style.map(
            "TRadiobutton",
            foreground=[("focus", UI_THEME["focus_ring"]), ("disabled", UI_THEME["disabled_ink"])],
            background=[("focus", UI_THEME["accent_soft"])],
            indicatorcolor=[("selected", UI_THEME["accent"]), ("active", UI_THEME["accent_soft"])],
        )
        style.configure("Footer.TFrame", background=UI_THEME["surface"])
        style.configure(
            "FooterStatus.TLabel",
            background=UI_THEME["surface"],
            foreground=UI_THEME["muted"],
            font=(self.ui_font, 11),
        )
        style.configure(
            "FooterSummary.TLabel",
            background=UI_THEME["surface"],
            foreground=UI_THEME["ink"],
            font=(self.ui_font, 12, "bold"),
        )
        style.configure(
            "Horizontal.TProgressbar",
            background=UI_THEME["accent"],
            troughcolor=UI_THEME["accent_soft"],
            bordercolor=UI_THEME["accent_soft"],
        )
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=UI_THEME["border"],
            troughcolor=UI_THEME["canvas"],
            bordercolor=UI_THEME["section_border"],
            lightcolor=UI_THEME["border"],
            darkcolor=UI_THEME["border"],
            arrowcolor=UI_THEME["ink"],
            relief="flat",
        )  # 滚动条使用同一深色主题，避免 Windows 原生亮色轨道破坏整体层级。
        style.map(
            "Dark.Vertical.TScrollbar",
            background=[("pressed", UI_THEME["accent_active"]), ("active", UI_THEME["button_hover"])],
            arrowcolor=[("pressed", UI_THEME["ink"]), ("active", UI_THEME["secondary_ink"])],
        )  # 悬停和按下状态保留足够反馈，但不抢过主按钮。

    def _build_layout(self) -> None:
        """Create a scrollable workbench with a fixed action/status footer."""

        shell = ttk.Frame(self.root)
        shell.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        self.content_canvas = tk.Canvas(
            shell,
            background=UI_THEME["canvas"],
            highlightthickness=0,
            borderwidth=0,
            yscrollincrement=24,
        )
        self.content_canvas.grid(row=0, column=0, sticky="nsew")
        self.content_scrollbar = ttk.Scrollbar(
            shell,
            orient="vertical",
            command=self.content_canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.content_scrollbar.grid(row=0, column=1, sticky="ns")
        self.content_canvas.configure(yscrollcommand=self.content_scrollbar.set)
        container = ttk.Frame(self.content_canvas, padding=20)
        self.content_container = container
        self.content_window = self.content_canvas.create_window(
            (0, 0), window=container, anchor="nw"
        )
        container.bind("<Configure>", self._update_content_scrollregion)
        self.content_canvas.bind("<Configure>", self._fit_content_to_viewport)
        self.root.bind("<MouseWheel>", self._on_content_mousewheel, add="+")
        self.root.bind("<Button-4>", self._on_content_mousewheel, add="+")
        self.root.bind("<Button-5>", self._on_content_mousewheel, add="+")
        container.columnconfigure(0, weight=1)

        ttk.Label(container, text="协议 PDF 差异对比工具", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.subtitle_label = ttk.Label(
            container,
            text="并排设置旧版与新版文档；报告识别、配对和差异标记保持原有逻辑。",
            style="Status.TLabel",
        )
        self.subtitle_label.grid(row=1, column=0, sticky="w", pady=(6, 18))

        self.document_cards_host = ttk.Frame(container)
        self.document_cards_host.grid(row=2, column=0, sticky="ew")
        self.old_document_card = self._create_document_card(
            self.document_cards_host,
            side="old",
            title="旧版 PDF",
            path_var=self.old_pdf_var,
            browse_command=self._browse_old_pdf,
            mode_var=self.old_page_mode_var,
            start_var=self.old_start_var,
            end_var=self.old_end_var,
        )
        self.new_document_card = self._create_document_card(
            self.document_cards_host,
            side="new",
            title="新版 PDF",
            path_var=self.new_pdf_var,
            browse_command=self._browse_new_pdf,
            mode_var=self.new_page_mode_var,
            start_var=self.new_start_var,
            end_var=self.new_end_var,
        )
        self._apply_responsive_layout(self.design_window_size[0])

        self.advanced_toggle = ttk.Button(
            container,
            text="高级设置  ▸",
            command=self._toggle_advanced,
        )
        self.advanced_toggle.grid(row=3, column=0, sticky="w", pady=(14, 8))
        self.input_widgets.append(self.advanced_toggle)
        self.advanced_body = self._create_elevated_section(
            container, row=4, text="高级设置", pady=(0, 14)
        )
        self.advanced_body.master.grid_remove()
        self._build_advanced_settings(self.advanced_body)

        footer = ttk.Frame(shell, style="Footer.TFrame", padding=(20, 12))
        footer.grid(row=1, column=0, columnspan=2, sticky="ew")
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=0)
        self.result_summary_label = ttk.Label(
            footer,
            textvariable=self.summary_var,
            style="FooterSummary.TLabel",
            wraplength=760,
        )
        self.result_summary_label.grid(row=0, column=0, sticky="w")
        self.result_path_label = ttk.Label(
            footer,
            textvariable=self.report_path_var,
            style="FooterStatus.TLabel",
            wraplength=760,
        )
        self.result_path_label.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.status_label = ttk.Label(
            footer, textvariable=self.status_var, style="FooterStatus.TLabel"
        )
        self.status_label.grid(row=2, column=0, sticky="w", pady=(7, 0))
        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=1)
        self.progress.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.progress.grid_remove()

        actions = ttk.Frame(footer, style="Footer.TFrame")
        actions.grid(row=0, column=1, rowspan=4, sticky="e", padx=(18, 0))
        self.run_button = ttk.Button(
            actions,
            text="开始比较",
            style="Primary.TButton",
            command=self.run_comparison,
        )
        self.run_button.grid(row=0, column=0, sticky="ew")
        self.input_widgets.append(self.run_button)
        self.open_html_button = ttk.Button(
            actions, text="打开 HTML 报告", command=self.open_html_report
        )
        self.open_html_button.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.open_html_button.grid_remove()
        self.open_dir_button = ttk.Button(
            actions, text="打开输出目录", command=self.open_report_directory
        )
        self.open_dir_button.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.open_dir_button.grid_remove()

    def _create_document_card(
        self,
        parent: ttk.Frame,
        *,
        side: str,
        title: str,
        path_var: tk.StringVar,
        browse_command: object,
        mode_var: tk.StringVar,
        start_var: tk.StringVar,
        end_var: tk.StringVar,
    ) -> tk.Frame:
        """Build one self-contained PDF selector and page-range card."""

        card = tk.Frame(
            parent,
            background=UI_THEME["surface_raised"],
            highlightthickness=1,
            highlightbackground=UI_THEME["section_border"],
        )
        card.columnconfigure(0, weight=1)
        body = ttk.Frame(card, style="SectionBody.TFrame", padding=14)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        ttk.Label(body, text=title, style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 10)
        )
        path_row = ttk.Frame(body, style="SectionBody.TFrame")
        path_row.grid(row=1, column=0, sticky="ew")
        path_row.columnconfigure(0, weight=1)
        path_entry = ttk.Entry(path_row, textvariable=path_var)
        path_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        browse_button = ttk.Button(
            path_row, text="选择 PDF", style="Browse.TButton", command=browse_command
        )
        browse_button.grid(row=0, column=1)
        self.file_browse_buttons.append(browse_button)
        self.input_widgets.extend((path_entry, browse_button))

        mode_row = ttk.Frame(body, style="SectionBody.TFrame")
        mode_row.grid(row=2, column=0, sticky="w", pady=(12, 0))
        all_button = ttk.Radiobutton(
            mode_row,
            text="全部页面",
            variable=mode_var,
            value="all",
            command=lambda selected=side: self._sync_page_mode(selected),
        )
        all_button.grid(row=0, column=0, sticky="w", padx=(0, 18))
        range_button = ttk.Radiobutton(
            mode_row,
            text="指定范围",
            variable=mode_var,
            value="range",
            command=lambda selected=side: self._sync_page_mode(selected),
        )
        range_button.grid(row=0, column=1, sticky="w")
        self.input_widgets.extend((all_button, range_button))

        range_frame = ttk.Frame(body, style="SectionBody.TFrame")
        range_frame.grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Label(range_frame, text="起始页").grid(row=0, column=0, sticky="w")
        start_entry = self._create_page_entry(range_frame, start_var)
        start_entry.grid(row=0, column=1, padx=(7, 14), ipady=3)
        ttk.Label(range_frame, text="终止页").grid(row=0, column=2, sticky="w")
        end_entry = self._create_page_entry(range_frame, end_var)
        end_entry.grid(row=0, column=3, padx=(7, 0), ipady=3)
        prefix = "旧协议" if side == "old" else "新协议"
        self.page_entry_widgets[f"{prefix}起始页"] = start_entry
        self.page_entry_widgets[f"{prefix}终止页"] = end_entry
        self.input_widgets.extend((start_entry, end_entry))
        setattr(self, f"{side}_range_frame", range_frame)
        range_frame.grid_remove()
        return card

    def _build_advanced_settings(self, parent: ttk.Frame) -> None:
        """Populate the advanced panel while keeping it collapsed by default."""

        parent.columnconfigure(1, weight=1)
        self._add_file_row(parent, 0, "输出目录", self.output_dir_var, self._browse_output_dir)
        settings_row = ttk.Frame(parent, style="SectionBody.TFrame")
        settings_row.grid(row=1, column=0, columnspan=3, sticky="ew", padx=2)
        self._add_setting_entry(settings_row, 0, 0, "章节匹配阈值", self.min_similarity_var)
        self._add_setting_entry(settings_row, 0, 2, "每章片段数", self.max_snippets_var)
        unchanged = ttk.Checkbutton(
            settings_row, text="列出未变化章节", variable=self.include_unchanged_var
        )
        unchanged.grid(row=0, column=4, sticky="w", padx=(10, 6), pady=10)
        auto_open = ttk.Checkbutton(
            settings_row, text="完成后自动打开报告", variable=self.auto_open_var
        )
        auto_open.grid(row=0, column=5, sticky="w", padx=(10, 0), pady=10)
        self.input_widgets.extend((unchanged, auto_open))

    def _toggle_advanced(self) -> None:
        """Expand or collapse optional settings without changing their values."""

        self.advanced_expanded = not self.advanced_expanded
        card = self.advanced_body.master
        if self.advanced_expanded:
            card.grid()
            self.advanced_toggle.configure(text="高级设置  ▾")
        else:
            card.grid_remove()
            self.advanced_toggle.configure(text="高级设置  ▸")
        self._update_content_scrollregion()

    def _sync_page_mode(self, side: str) -> None:
        """Show range entries only when that document uses an explicit range."""

        mode_var = self.old_page_mode_var if side == "old" else self.new_page_mode_var
        frame = self.old_range_frame if side == "old" else self.new_range_frame
        if mode_var.get() == "range":
            frame.grid()
        else:
            frame.grid_remove()
        self._update_content_scrollregion()

    def _apply_responsive_layout(self, width: int) -> None:
        """Place document cards side by side, or stack them below 900px."""

        mode = "stacked" if width < 900 else "side-by-side"
        self.old_document_card.grid_forget()
        self.new_document_card.grid_forget()
        if mode == "side-by-side":
            self.document_cards_host.columnconfigure(0, weight=1)
            self.document_cards_host.columnconfigure(1, weight=1)
            self.old_document_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
            self.new_document_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        else:
            self.document_cards_host.columnconfigure(0, weight=1)
            self.document_cards_host.columnconfigure(1, weight=0)
            self.old_document_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
            self.new_document_card.grid(row=1, column=0, sticky="ew")
        self.document_layout_mode = mode

    def _update_content_scrollregion(self, _event: tk.Event | None = None) -> None:
        """Refresh the scrollable area after any child changes its requested size."""

        bounds = self.content_canvas.bbox("all")  # 读取当前所有 Canvas 子项的实际边界。
        if bounds is not None:
            self.content_canvas.configure(scrollregion=bounds)  # 只有存在内容时才写入合法滚动范围。
            content_height = max(0, bounds[3] - bounds[1])
            viewport_height = self.content_canvas.winfo_height()
            if viewport_height > 1 and content_height <= viewport_height:
                self.content_canvas.yview_moveto(0.0)
                self.content_scrollbar.grid_remove()
            else:
                self.content_scrollbar.grid()

    def _fit_content_to_viewport(self, event: tk.Event) -> None:
        """Stretch content to the viewport and update text wrapping responsively."""

        self._apply_responsive_layout(event.width)
        self.content_canvas.itemconfigure(
            self.content_window,
            width=event.width,
        )  # 宽度贴合视口；内容保留自然高度，窄屏重排后 Canvas 才能获得真实滚动范围。
        wrap_width = max(320, event.width - 40)  # 扣除容器左右 padding，保留小屏可读的最小换行宽度。
        self.subtitle_label.configure(wraplength=wrap_width)  # 副标题不再依赖平台默认文本宽度。
        result_wrap_width = max(260, min(760, event.width - 330))
        self.result_summary_label.configure(wraplength=result_wrap_width)  # 摘要随窗口宽度重新排版。
        self.result_path_label.configure(wraplength=result_wrap_width)  # 长路径随窗口宽度重新排版。
        self._update_content_scrollregion()  # 宽度变化可能改变文字高度，需要立即刷新滚动范围。

    def _on_content_mousewheel(self, event: tk.Event) -> str | None:
        """Scroll the form with Windows/macOS wheel events and Linux X11 buttons."""

        button_number = getattr(event, "num", None)  # Linux 把滚轮方向编码在 Button-4/5，而非 delta。
        if button_number == 4:
            units = -1  # Button-4 表示内容向上移动一格。
        elif button_number == 5:
            units = 1  # Button-5 表示内容向下移动一格。
        else:
            delta = int(getattr(event, "delta", 0))  # Windows 常见 ±120，macOS 触控板可能返回较小数值。
            if delta == 0:
                return None  # 没有方向的合成事件不拦截其它控件行为。
            units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)  # 统一正值向上、负值向下。
        self.content_canvas.yview_scroll(units, "units")  # 通过 Canvas 公共滚动接口更新视口和滚动条滑块。
        return "break"  # 防止同一次滚轮事件继续冒泡并重复滚动。

    def _create_elevated_section(
        self,
        parent: ttk.Frame,
        *,
        row: int,
        text: str,
        pady: tuple[int, int],
    ) -> ttk.Frame:
        """Create one tonal-elevation section without directional shadows or bevels."""

        # 深色界面的层次由画布、卡片和输入区三档明度建立；均匀轮廓只负责收边。
        card = tk.Frame(
            parent,
            background=UI_THEME["surface_raised"],
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=UI_THEME["section_border"],
        )
        card.grid(row=row, column=0, sticky="ew", pady=pady)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        ttk.Label(card, text=text, style="SectionTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=12, pady=(8, 3)
        )
        section = ttk.Frame(card, style="SectionBody.TFrame", padding=(8, 0, 8, 8))
        section.grid(row=1, column=0, sticky="nsew")
        return section

    def _add_file_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        command: object,
    ) -> None:
        """Add one path entry row with a browse button."""

        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=8)
        path_entry = ttk.Entry(parent, textvariable=variable)  # 路径输入框允许用户直接粘贴 PDF 或目录路径。
        path_entry.grid(
            row=row, column=1, sticky="ew", padx=(0, 10), pady=8  # 输入框横向拉伸，长路径也能看清。
        )
        browse_button = ttk.Button(
            parent,
            text="选择",
            style="Browse.TButton",
            command=command,
        )  # “选择”按钮以独立次级操作色呈现，不再融入卡片背景。
        browse_button.grid(
            row=row, column=2, sticky="e", padx=(0, 10), pady=8  # 按钮固定在每行右侧。
        )
        self.file_browse_buttons.append(browse_button)  # 记录按钮，避免未来打包时漏掉选择控件。
        self.input_widgets.extend((path_entry, browse_button))

    def _create_page_entry(self, parent: ttk.Frame, variable: tk.StringVar) -> tk.Entry:
        """Create a native page-number entry with a visible edit affordance."""

        entry = tk.Entry(
            parent,  # 原生 Entry 在 macOS 上比 ttk.Entry 的焦点/输入表现更直接。
            textvariable=variable,  # 绑定到对应页码变量，collect_config 会读取这些值。
            font=(self.ui_font, 12),  # 显式绑定跨平台字体，避免打包 Windows 时使用另一套默认字形。
            width=12,  # 比旧版略宽，避免用户误以为只是窄标签。
            justify="center",  # 页码通常较短，居中显示更像可编辑数字框。
            relief="solid",  # 明确画出边框，减少“不知道哪里能输入”的问题。
            borderwidth=1,  # 保持边框克制，不让界面显得很重。
            highlightthickness=1,  # 焦点边框让当前编辑框更明显。
            background=UI_THEME["input"],
            foreground=UI_THEME["ink"],
            highlightbackground=UI_THEME["border_strong"],
            highlightcolor=UI_THEME["accent"],
            selectbackground=UI_THEME["accent_soft"],
            selectforeground=UI_THEME["ink"],
            insertbackground=UI_THEME["ink"],
            insertwidth=2,  # 光标稍宽，便于看出输入焦点。
            takefocus=True,  # 允许 Tab 键切换到页码框。
        )
        return entry  # 返回真实可编辑控件，调用方负责布局和保存引用。

    def _add_setting_entry(
        self,
        parent: ttk.Frame,
        row: int,
        column: int,
        label: str,
        variable: tk.StringVar,
    ) -> None:
        """Add one compact advanced setting field."""

        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=10, pady=10)
        entry = ttk.Entry(parent, textvariable=variable, width=8)
        entry.grid(
            row=row, column=column + 1, sticky="w", padx=(0, 8), pady=10
        )
        self.input_widgets.append(entry)

    def _browse_old_pdf(self) -> None:
        self._browse_pdf(self.old_pdf_var, "选择旧协议 PDF")

    def _browse_new_pdf(self) -> None:
        self._browse_pdf(self.new_pdf_var, "选择新协议 PDF")

    def _browse_pdf(self, variable: tk.StringVar, title: str) -> None:
        """Open a PDF picker and update the matching path variable."""

        path = filedialog.askopenfilename(
            title=title,
            filetypes=(("PDF 文件", "*.pdf"), ("所有文件", "*.*")),
        )
        if path:
            variable.set(path)

    def _browse_output_dir(self) -> None:
        """Open a directory picker for timestamped report folders."""

        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.output_dir_var.set(path)

    def collect_config(self) -> DesktopRunConfig:
        """Validate the current form and create a runnable configuration."""

        old_pdf = Path(self.old_pdf_var.get()).expanduser()
        new_pdf = Path(self.new_pdf_var.get()).expanduser()
        output_dir_text = self.output_dir_var.get().strip()
        if not old_pdf.exists():
            raise FileNotFoundError(f"旧协议 PDF 路径无效: {old_pdf}")
        if not new_pdf.exists():
            raise FileNotFoundError(f"新协议 PDF 路径无效: {new_pdf}")
        if not output_dir_text:
            raise ValueError("输出目录不能为空。")
        output_dir = Path(output_dir_text).expanduser().resolve()

        old_start, old_end = page_range_values(
            self.old_page_mode_var.get(),
            self.old_start_var.get(),
            self.old_end_var.get(),
            "旧协议",
        )
        new_start, new_end = page_range_values(
            self.new_page_mode_var.get(),
            self.new_start_var.get(),
            self.new_end_var.get(),
            "新协议",
        )

        options = DiffOptions(
            min_section_match_similarity=parse_positive_float(
                self.min_similarity_var.get(), "章节匹配阈值"
            ),
            max_snippets_per_section=parse_positive_int(
                self.max_snippets_var.get(), "每章展示片段数"
            ),
            include_unchanged_sections=self.include_unchanged_var.get(),
            old_start_page=old_start,
            old_end_page=old_end,
            new_start_page=new_start,
            new_end_page=new_end,
            # GUI 保持默认解析策略；CLI/API 仍支持显式 Tesseract 语言代码。
            ocr_language=None,
        )
        return DesktopRunConfig(old_pdf=old_pdf, new_pdf=new_pdf, output_dir=output_dir, options=options)

    def run_comparison(self) -> None:
        """Validate inputs and run the comparison in a background thread."""

        try:
            config = self.collect_config()
        except (FileNotFoundError, ValueError) as exc:
            messagebox.showerror("输入有误", str(exc))
            self.status_var.set("请修正输入后重新运行。")
            return

        self._set_running(True)
        self._handle_progress(ProgressEvent(stage="read_old", side="old"))
        # 先让按钮禁用、进度条和忙碌光标刷新出来，再启动耗时任务，减少“点击后卡住”的感觉。
        self.root.after(40, self._start_worker, config)

    def _start_worker(self, config: DesktopRunConfig) -> None:
        """Start the background comparison after the UI has repainted."""

        worker = threading.Thread(target=self._run_worker, args=(config,), daemon=True)
        try:
            worker.start()
        except RuntimeError as exc:
            self._set_running(False)
            self._handle_error(RuntimeError(f"无法启动后台任务: {exc}"))
            return
        self.root.after(250, self._poll_result_queue)

    def _run_worker(self, config: DesktopRunConfig) -> None:
        """Background worker that keeps the Tk event loop responsive."""

        try:
            observer = lambda event: self._result_queue.put(("progress", event))
            result = run_diff(
                config.old_pdf,
                config.new_pdf,
                config.options,
                progress_observer=observer,
            )
            notify_progress(observer, ProgressEvent(stage="report"))
            outputs = write_reports(result, config.output_dir, config.options)
        except (FileNotFoundError, MissingDependencyError, PdfReadError, ValueError) as exc:
            self._result_queue.put(("error", exc))
        except Exception as exc:  # pragma: no cover - last-resort UI diagnostics.
            self._result_queue.put(("error", RuntimeError(f"运行失败: {exc}")))
        else:
            self._result_queue.put(("success", DesktopRunSuccess(result=result, outputs=outputs)))

    def _poll_result_queue(self) -> None:
        """Handle background completion on the Tk main thread."""

        terminal: tuple[str, object] | None = None
        while True:
            try:
                status, payload = self._result_queue.get_nowait()
            except queue.Empty:
                break
            if status == "progress" and isinstance(payload, ProgressEvent):
                self._handle_progress(payload)
            else:
                terminal = (status, payload)
        if terminal is None:
            self.root.after(250, self._poll_result_queue)
            return

        self._set_running(False)
        status, payload = terminal
        if status == "success" and isinstance(payload, DesktopRunSuccess):
            self._handle_success(payload)
        else:
            self._handle_error(payload)

    def _elapsed_seconds(self) -> int:
        if self._run_started_at is None:
            return 0
        return max(0, int(time.monotonic() - self._run_started_at))

    def _handle_progress(self, event: ProgressEvent) -> None:
        """Render only real extraction page counts; later stages show no percentage."""

        self._current_progress_event = event
        stage_names = {
            "read_old": "读取旧版",
            "read_new": "读取新版",
            "match_diff": "匹配差异",
            "visual_evidence": "生成视觉证据",
            "report": "生成报告",
        }
        stage_name = stage_names.get(event.stage, event.stage)
        elapsed = self._elapsed_seconds()
        if event.completed_pages is not None and event.total_pages is not None:
            total = max(1, event.total_pages)
            self.progress.configure(maximum=total, value=min(event.completed_pages, total))
            self.progress.grid()
            self.status_var.set(
                f"运行 · {stage_name} · 第 {event.completed_pages}/{event.total_pages} 页 · 已用 {elapsed} 秒"
            )
        else:
            self.progress.grid_remove()
            self.status_var.set(f"运行 · {stage_name} · 已用 {elapsed} 秒")

    def _tick_elapsed(self) -> None:
        """Refresh elapsed time while preserving the latest real stage/page event."""

        self._elapsed_after_id = None
        if not self.is_running:
            return
        if self._current_progress_event is not None:
            self._handle_progress(self._current_progress_event)
        self._elapsed_after_id = self.root.after(1000, self._tick_elapsed)

    def _handle_success(self, payload: DesktopRunSuccess) -> None:
        """Update result controls after a successful comparison."""

        self._last_outputs = payload.outputs
        assessment = payload.result.assessment
        if assessment is None:
            assessment_note = "无法判断（缺少可靠性评估）"
            status_text = "报告已生成，但当前结果无法判断。"
        elif assessment.state is ReliabilityState.RELIABLE:
            assessment_note = "识别可靠"
            status_text = "可靠比较完成。"
        elif assessment.state is ReliabilityState.DEGRADED:
            assessment_note = "需人工复核"
            status_text = "报告已生成；识别存在风险，请人工复核。"
        else:
            assessment_note = "无法判断"
            status_text = "报告已生成，但当前文档无法可靠识别。"
        reader_summary = _reported_reader_summary(payload.outputs)
        if reader_summary is not None:
            body_note = (
                f"正文章节差异 {reader_summary.body_changes}（"
                f"章节修改 {reader_summary.modified}，"
                f"章节新增 {reader_summary.added}，"
                f"章节删除 {reader_summary.deleted}）"
            )
            table_note = f"，表格变化 {reader_summary.table_changes}"
            visual_note = f"，视觉差异项 {reader_summary.visual_items}"
        else:
            body_note = "正文和表格计数请查看报告"
            table_note = ""
            visual_note = "；视觉差异项请查看报告"
        if assessment is not None and assessment.state is not ReliabilityState.RELIABLE:
            visual_note += "；视觉校对需人工复核（见报告）"
        self.summary_var.set(
            f"{assessment_note}："
            f"{body_note}"
            f"{table_note}"
            f"{visual_note}"
        )
        html_path = payload.outputs["html"]
        self.report_path_var.set(f"HTML 报告: {html_path}")
        self.status_var.set(status_text)
        self.open_html_button.configure(state="normal")
        self.open_dir_button.configure(state="normal")
        self.open_html_button.grid()
        self.open_dir_button.grid()
        if getattr(self, "auto_open_var", None) is not None and self.auto_open_var.get():
            self.open_html_report()

    def _handle_error(self, payload: object) -> None:
        """Show a user-readable error from the background worker."""

        message = str(payload)
        first_line = next(
            (line.strip() for line in message.splitlines() if line.strip()),
            "未知错误",
        )
        inline_message = " ".join(first_line.split())
        if len(inline_message) > 150:
            inline_message = f"{inline_message[:149]}…"
        self.summary_var.set(f"本次比较未生成报告：{inline_message}")
        self.report_path_var.set("")
        self.status_var.set("失败 · 请检查提示后重新运行。")
        self.open_html_button.grid_remove()
        self.open_dir_button.grid_remove()
        messagebox.showerror("运行失败", message)

    def _set_running(self, running: bool) -> None:
        """Lock all mutable controls while keeping the fixed footer visible."""

        if running:
            if self._elapsed_after_id is not None:
                self.root.after_cancel(self._elapsed_after_id)
                self._elapsed_after_id = None
            self.is_running = True
            self._run_started_at = time.monotonic()
            self._current_progress_event = None
            self._input_states = {
                widget: str(widget.cget("state")) for widget in self.input_widgets
            }
            for widget in self.input_widgets:
                widget.configure(state="disabled")
            self.open_html_button.configure(state="disabled")
            self.open_dir_button.configure(state="disabled")
            self.open_html_button.grid_remove()
            self.open_dir_button.grid_remove()
            self.root.configure(cursor="watch")
            self._elapsed_after_id = self.root.after(1000, self._tick_elapsed)
        else:
            self.is_running = False
            if self._elapsed_after_id is not None:
                self.root.after_cancel(self._elapsed_after_id)
                self._elapsed_after_id = None
            for widget in self.input_widgets:
                widget.configure(state=self._input_states.get(widget, "normal"))
            self.root.configure(cursor="")
            self.progress.grid_remove()
            self._current_progress_event = None
            self._run_started_at = None

    def open_html_report(self) -> None:
        """Open the latest HTML report in the user's default browser."""

        if not self._last_outputs:
            return
        html_path = self._last_outputs["html"].expanduser().resolve()
        try:
            opened = webbrowser.open(html_path.as_uri())
        except (OSError, webbrowser.Error, ValueError) as exc:
            messagebox.showwarning("无法打开报告", f"报告已生成，但无法自动打开：{exc}")
            return
        if not opened:
            messagebox.showwarning(
                "无法打开报告",
                f"报告已生成，但系统没有接受打开请求。\n请手动打开：{html_path}",
            )

    def open_report_directory(self) -> None:
        """Open the latest report directory in the platform file manager."""

        if not self._last_outputs:
            return
        report_dir = self._last_outputs["report_dir"].expanduser().resolve()
        if not report_dir.is_dir():
            messagebox.showwarning("无法打开目录", f"输出目录不存在：{report_dir}")
            return
        try:
            opened = open_path(report_dir)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            messagebox.showwarning("无法打开目录", f"无法调用系统文件管理器：{exc}")
            return
        if not opened:
            messagebox.showwarning(
                "无法打开目录",
                f"系统没有接受打开请求。\n请手动打开：{report_dir}",
            )

    def _on_close_requested(self) -> None:
        """Prevent closing while the report writer can still be committing files."""

        if self.is_running:
            messagebox.showwarning(
                "比较仍在运行",
                "为保证报告文件完整，请等待本次比较完成后再关闭窗口。",
            )
            return
        self.root.destroy()


def open_path(path: Path) -> bool:
    """Open a file or directory with the current operating system shell."""

    resolved = str(path.resolve())
    if sys.platform == "darwin":
        return subprocess.run(["open", resolved], check=False).returncode == 0
    elif os.name == "nt":
        os.startfile(resolved)  # type: ignore[attr-defined]
        return True
    else:
        return subprocess.run(["xdg-open", resolved], check=False).returncode == 0


def run_smoke_test() -> None:
    """Instantiate the GUI without entering the main loop.

    Packaging tests use this to verify that all widgets can be created, styled,
    and validated in the bundled Python runtime without requiring a human to
    click through the application.
    """

    root = create_tk_root()  # 冻结 EXE 自检也必须复现真实启动前的 DPI 配置顺序。
    app = ProtocolDiffDesktopApp(root)
    assert root.title() == "协议 PDF 差异对比工具"
    assert app.output_dir_var.get()
    root.update_idletasks()  # 先让 Tk 完成布局，后续才能检查控件是否真正挂到 grid 上。
    assert app.run_button.cget("text") == "开始比较"  # 确认主按钮不是旧文案或旧界面。
    assert app.run_button.cget("command")  # 确认主按钮绑定了回调，而不是只有静态文字。
    widget_texts = collect_widget_texts(root)  # 收集所有可见控件文案，检查关键输入是否存在。
    required_labels = {
        "旧版 PDF",
        "新版 PDF",
        "全部页面",
        "指定范围",
        "开始比较",
    }
    missing_labels = sorted(required_labels - widget_texts)  # 找出缺失控件，方便构建失败时定位。
    assert not missing_labels, f"桌面界面缺少关键控件: {', '.join(missing_labels)}"  # 缺控件时直接失败。
    assert len(app.file_browse_buttons) == 3  # 旧 PDF、新 PDF、输出目录都必须有选择按钮。
    assert app.document_layout_mode in {"side-by-side", "stacked"}
    assert not app.advanced_expanded
    assert app.ui_font in set(tkfont.families(root))  # 实际字体必须存在，不能让 Windows 悄悄回退不存在的 PingFang。
    assert app.initial_window_size[0] <= root.winfo_screenwidth()  # 初始窗口不能超出 DPI 换算后的逻辑屏幕宽度。
    assert app.initial_window_size[1] <= root.winfo_screenheight()  # 初始窗口不能超出 DPI 换算后的逻辑屏幕高度。
    assert app.content_canvas.cget("yscrollcommand")  # 缩放后内容超高时必须仍有可用滚动路径。
    assert app.content_scrollbar.winfo_exists()  # 可发现的滚动条必须进入真实控件树。
    for label, entry in app.page_entry_widgets.items():
        assert entry.winfo_class() == "Entry", f"{label} 不是输入框"  # 防止标签存在但输入框丢失。
        assert entry.winfo_exists(), f"{label} 未加入控件树"
        assert str(entry.cget("state")) != "disabled", f"{label} 被禁用"  # 防止输入框看得见但用户不能编辑。
        entry.focus_force()  # 强制聚焦输入框，模拟用户点击后准备输入。
        entry.delete(0, tk.END)  # 清空输入框，模拟用户准备输入页码。
        entry.insert(0, "2")  # 用 Tk 的文本插入接口验证控件可写，避免 Windows runner 不派发按键字符。
        root.update_idletasks()  # 处理布局和控件状态更新，确认输入框值已经变化。
        assert entry.get() == "2", f"{label} 无法输入页码"  # 如果 Entry 被错误禁用，这里会暴露。
        entry_family = str(
            tkfont.Font(root=root, font=entry.cget("font")).actual("family")
        )  # 解析控件最终字体，避免只检查配置字符串却仍发生系统回退。
        assert entry_family == app.ui_font, f"{label} 未使用统一界面字体"  # 原生 Tk 输入框必须与 ttk 文本一致。
    root.destroy()


def collect_widget_texts(widget: tk.Widget) -> set[str]:
    """Collect visible widget labels for smoke tests and packaged checks."""

    texts: set[str] = set()  # 保存当前控件及子控件的所有可见文字。
    try:
        text = widget.cget("text")  # Tk/ttk 的 Label、Button、Frame 等通常都有 text 属性。
    except tk.TclError:
        text = ""  # Entry 等控件没有 text 属性时跳过即可。
    if isinstance(text, str) and text:
        texts.add(text)  # 只记录非空文字，避免无意义空字符串干扰检查。
    for child in widget.winfo_children():
        texts.update(collect_widget_texts(child))  # 递归检查嵌套区域里的按钮和标签。
    return texts  # 返回完整文案集合，供 smoke test 断言关键控件。


def main() -> int:
    """Launch the desktop application."""

    root = create_tk_root()  # 源码运行与 Windows EXE 使用同一 DPI-aware 根窗口工厂。
    ProtocolDiffDesktopApp(root)
    root.mainloop()
    return 0
