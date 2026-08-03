"""Tkinter desktop interface for the protocol PDF diff workflow.

The command-line entry point remains useful for automation, but most reviewers
need a small desktop tool: choose two PDFs, optionally type page ranges, run the
comparison, then open the HTML report. This module keeps that GUI thin and
delegates all PDF and diff behavior to the same core pipeline that the tests
already exercise.
"""

from __future__ import annotations

import json
import os
import queue
import math  # GUI 在启动后台任务前拒绝 NaN、Inf 和超出比例区间的阈值。
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from .compare import run_diff
from .models import DiffOptions, DiffResult
from .pdf_extract import MissingDependencyError, PdfReadError
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


def _reported_table_change_count(outputs: dict[str, Path]) -> int | None:
    """Read the already-written report model so the GUI summary covers tables."""

    json_path = outputs.get("json")
    if json_path is None:
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        table_changes = payload.get("table_changes")
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        return None
    return len(table_changes) if isinstance(table_changes, list) else None


class ProtocolDiffDesktopApp:
    """Desktop GUI coordinator for selecting PDFs and launching comparisons."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("协议 PDF 差异对比工具")
        self.root.minsize(980, 700)

        self.old_pdf_var = tk.StringVar()
        self.new_pdf_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(default_output_dir()))  # 默认输出到用户可写目录。
        self.old_start_var = tk.StringVar()
        self.old_end_var = tk.StringVar()
        self.new_start_var = tk.StringVar()
        self.new_end_var = tk.StringVar()
        self.min_similarity_var = tk.StringVar(value="0.72")
        self.max_snippets_var = tk.StringVar(value="20")
        self.include_unchanged_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="请选择旧版和新版 PDF，设置范围后开始比较。")
        self.summary_var = tk.StringVar(value="尚未生成报告")
        self.report_path_var = tk.StringVar(value="")

        self._last_outputs: dict[str, Path] | None = None
        self._result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.page_entry_widgets: dict[str, tk.Entry] = {}  # 保存四个原生页码输入框，供 smoke test 检查真实输入能力。
        self.file_browse_buttons: list[ttk.Button] = []  # 保存三个“选择”按钮，供打包后自测确认按钮存在。

        self._configure_style()
        self._build_layout()

    def _configure_style(self) -> None:
        """Apply a precise, high-contrast visual system without moving the layout."""

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            # 固定控件渲染，避免不同系统主题改变输入框、边框和禁用态的层级。
            style.theme_use("clam")
        self.root.configure(background=UI_THEME["canvas"])
        style.configure(
            ".",
            font=("PingFang SC", 12),
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
            font=("PingFang SC", 22, "bold"),
        )
        style.configure(
            "Status.TLabel",
            background=UI_THEME["canvas"],
            foreground=UI_THEME["muted"],
            font=("PingFang SC", 12),
        )
        style.configure(
            "SectionBody.TFrame",
            background=UI_THEME["surface_raised"],
        )
        style.configure(
            "SectionTitle.TLabel",
            background=UI_THEME["surface_raised"],
            foreground=UI_THEME["ink"],
            font=("PingFang SC", 12, "bold"),
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
            font=("PingFang SC", 12, "bold"),
            padding=(14, 7),
        )
        style.map(
            "Browse.TButton",
            background=[
                ("active", UI_THEME["browse_button_hover"]),
                ("pressed", UI_THEME["browse_button_active"]),
            ],
            bordercolor=[
                ("focus", UI_THEME["accent"]),
                ("active", UI_THEME["browse_button_border"]),
            ],
        )
        style.configure(
            "Primary.TButton",
            background=UI_THEME["accent"],
            foreground=UI_THEME["accent_ink"],
            bordercolor=UI_THEME["accent"],
            lightcolor=UI_THEME["accent"],
            darkcolor=UI_THEME["accent"],
            relief="flat",
            font=("PingFang SC", 12, "bold"),
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
            foreground=[("disabled", UI_THEME["disabled_ink"])],
            indicatorcolor=[("selected", UI_THEME["accent"]), ("active", UI_THEME["accent_soft"])],
        )
        style.configure(
            "Horizontal.TProgressbar",
            background=UI_THEME["accent"],
            troughcolor=UI_THEME["accent_soft"],
            bordercolor=UI_THEME["accent_soft"],
        )
        style.configure(
            "ResultSummary.TLabel",
            background=UI_THEME["surface_raised"],
            foreground=UI_THEME["ink"],
            font=("PingFang SC", 12, "bold"),
        )
        style.configure(
            "ResultPath.TLabel",
            background=UI_THEME["surface_raised"],
            foreground=UI_THEME["muted"],
            font=("PingFang SC", 11),
        )

    def _build_layout(self) -> None:
        """Create the complete form and result controls."""

        container = ttk.Frame(self.root, padding=20)
        container.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        ttk.Label(container, text="协议 PDF 差异对比工具", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            container,
            text="选择两份协议 PDF，设置可选页码范围，生成 HTML / TXT / CSV / JSON 差异报告。",
            style="Status.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 18))

        files_frame = self._create_elevated_section(container, row=2, text="PDF 文件", pady=(0, 14))
        files_frame.columnconfigure(1, weight=1)
        self._add_file_row(files_frame, 0, "旧协议", self.old_pdf_var, self._browse_old_pdf)
        self._add_file_row(files_frame, 1, "新协议", self.new_pdf_var, self._browse_new_pdf)
        self._add_file_row(files_frame, 2, "输出目录", self.output_dir_var, self._browse_output_dir)

        ranges_frame = self._create_elevated_section(container, row=3, text="页码范围", pady=(0, 14))
        # 只让输入框所在列吸收剩余宽度，标签列保持内容宽度，避免文字和输入框被拉开。
        ranges_frame.columnconfigure(0, weight=0)
        ranges_frame.columnconfigure(1, weight=1)
        ranges_frame.columnconfigure(2, weight=0)
        ranges_frame.columnconfigure(3, weight=1)
        self._add_page_fields(
            ranges_frame,
            0,
            "旧协议起始页",
            self.old_start_var,
            "旧协议终止页",
            self.old_end_var,
        )
        self._add_page_fields(
            ranges_frame,
            1,
            "新协议起始页",
            self.new_start_var,
            "新协议终止页",
            self.new_end_var,
        )

        # 把主操作区前移到匹配设置之前，让用户首屏就能看到开始按钮。
        action_frame = self._create_elevated_section(container, row=4, text="开始生成报告", pady=(0, 14))
        action_frame.columnconfigure(4, weight=1)  # 右侧留出弹性空间，避免按钮挤压。
        self.run_button = ttk.Button(
            action_frame,  # 按钮放在“开始生成报告”区域内。
            text="开始比较",  # 按钮只保留核心动作，避免重复说明“生成报告”。
            style="Primary.TButton",  # 使用主按钮样式突出最常用操作。
            command=self.run_comparison,  # 点击后进入输入校验和后台比较流程。
        )
        self.run_button.grid(row=0, column=0, sticky="w", padx=10, pady=12)  # 固定在操作区最左侧。
        self.open_html_button = ttk.Button(
            action_frame,  # 报告按钮也放在同一操作区。
            text="打开 HTML 报告",  # 运行成功后直接打开最直观的 HTML 报告。
            command=self.open_html_report,  # 点击后用默认浏览器打开最近一次 HTML。
            state="disabled",  # 未生成报告前禁用，避免用户打开空路径。
        )
        self.open_html_button.grid(row=0, column=1, sticky="w", padx=(0, 10), pady=12)
        self.open_dir_button = ttk.Button(
            action_frame,  # 输出目录按钮放在报告按钮后面。
            text="打开输出目录",  # 方便用户查看 TXT/CSV/JSON 等其它文件。
            command=self.open_report_directory,  # 点击后打开最近一次报告目录。
            state="disabled",  # 未生成报告前禁用，避免打开无效目录。
        )
        self.open_dir_button.grid(row=0, column=2, sticky="w", pady=12)

        settings_frame = self._create_elevated_section(container, row=5, text="匹配设置", pady=(0, 14))
        for column in range(8):
            settings_frame.columnconfigure(column, weight=1)
        self._add_setting_entry(settings_frame, 0, 0, "章节匹配阈值", self.min_similarity_var)
        self._add_setting_entry(settings_frame, 0, 2, "每章展示片段数", self.max_snippets_var)
        ttk.Checkbutton(
            settings_frame,
            text="列出未变化章节",
            variable=self.include_unchanged_var,
        ).grid(row=0, column=4, columnspan=4, sticky="w", padx=8, pady=10)

        self.progress = ttk.Progressbar(container, mode="indeterminate")
        self.progress.grid(row=6, column=0, sticky="ew")
        ttk.Label(container, textvariable=self.status_var, style="Status.TLabel").grid(
            row=7, column=0, sticky="w", pady=(10, 4)
        )

        result_frame = self._create_elevated_section(container, row=8, text="结果", pady=(10, 0))
        container.rowconfigure(8, weight=1)
        result_frame.columnconfigure(0, weight=1)
        ttk.Label(result_frame, textvariable=self.summary_var, style="ResultSummary.TLabel", wraplength=820).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 6)
        )
        ttk.Label(result_frame, textvariable=self.report_path_var, style="ResultPath.TLabel", wraplength=820).grid(
            row=1, column=0, sticky="w", padx=12, pady=(0, 12)
        )

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
        card.grid(row=row, column=0, sticky="nsew" if row == 8 else "ew", pady=pady)
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

    def _add_page_fields(
        self,
        parent: ttk.Frame,
        row: int,
        start_label: str,
        start_var: tk.StringVar,
        end_label: str,
        end_var: tk.StringVar,
    ) -> None:
        """Add start/end page fields for one side of the comparison."""

        ttk.Label(parent, text=start_label).grid(
            row=row, column=0, sticky="w", padx=(10, 6), pady=8
        )
        start_entry = self._create_page_entry(parent, start_var)  # 起始页输入框，留空表示默认起点。
        start_entry.grid(
            row=row, column=1, sticky="w", padx=(0, 18), pady=8, ipady=4  # 加高输入框，点击区域更明确。
        )
        ttk.Label(parent, text=end_label).grid(
            row=row, column=2, sticky="w", padx=(10, 6), pady=8
        )
        end_entry = self._create_page_entry(parent, end_var)  # 终止页输入框，留空表示默认终点。
        end_entry.grid(
            row=row, column=3, sticky="w", padx=(0, 18), pady=8, ipady=4  # 与起始页输入框保持同样宽度。
        )
        self.page_entry_widgets[start_label] = start_entry  # 记录起始页控件，供回归测试直接验证可输入。
        self.page_entry_widgets[end_label] = end_entry  # 记录终止页控件，供回归测试直接验证可输入。

    def _create_page_entry(self, parent: ttk.Frame, variable: tk.StringVar) -> tk.Entry:
        """Create a native page-number entry with a visible edit affordance."""

        entry = tk.Entry(
            parent,  # 原生 Entry 在 macOS 上比 ttk.Entry 的焦点/输入表现更直接。
            textvariable=variable,  # 绑定到对应页码变量，collect_config 会读取这些值。
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
        ttk.Entry(parent, textvariable=variable, width=8).grid(
            row=row, column=column + 1, sticky="w", padx=(0, 8), pady=10
        )

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
        output_dir = Path(self.output_dir_var.get()).expanduser()
        if not old_pdf.exists():
            raise FileNotFoundError(f"旧协议 PDF 路径无效: {old_pdf}")
        if not new_pdf.exists():
            raise FileNotFoundError(f"新协议 PDF 路径无效: {new_pdf}")
        if not output_dir:
            raise ValueError("输出目录不能为空。")

        options = DiffOptions(
            min_section_match_similarity=parse_positive_float(
                self.min_similarity_var.get(), "章节匹配阈值"
            ),
            max_snippets_per_section=parse_positive_int(
                self.max_snippets_var.get(), "每章展示片段数"
            ),
            include_unchanged_sections=self.include_unchanged_var.get(),
            old_start_page=parse_optional_page(self.old_start_var.get(), "旧协议起始页"),
            old_end_page=parse_optional_page(self.old_end_var.get(), "旧协议终止页"),
            new_start_page=parse_optional_page(self.new_start_var.get(), "新协议起始页"),
            new_end_page=parse_optional_page(self.new_end_var.get(), "新协议终止页"),
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
        self.status_var.set("正在抽取 PDF 文本并比较章节...")
        # 先让按钮禁用、进度条和忙碌光标刷新出来，再启动耗时任务，减少“点击后卡住”的感觉。
        self.root.after(40, self._start_worker, config)

    def _start_worker(self, config: DesktopRunConfig) -> None:
        """Start the background comparison after the UI has repainted."""

        worker = threading.Thread(target=self._run_worker, args=(config,), daemon=True)
        worker.start()
        self.root.after(250, self._poll_result_queue)

    def _run_worker(self, config: DesktopRunConfig) -> None:
        """Background worker that keeps the Tk event loop responsive."""

        try:
            result = run_diff(config.old_pdf, config.new_pdf, config.options)
            outputs = write_reports(result, config.output_dir, config.options)
        except (FileNotFoundError, MissingDependencyError, PdfReadError, ValueError) as exc:
            self._result_queue.put(("error", exc))
        except Exception as exc:  # pragma: no cover - last-resort UI diagnostics.
            self._result_queue.put(("error", RuntimeError(f"运行失败: {exc}")))
        else:
            self._result_queue.put(("success", DesktopRunSuccess(result=result, outputs=outputs)))

    def _poll_result_queue(self) -> None:
        """Handle background completion on the Tk main thread."""

        try:
            status, payload = self._result_queue.get_nowait()
        except queue.Empty:
            self.root.after(250, self._poll_result_queue)
            return

        self._set_running(False)
        if status == "success" and isinstance(payload, DesktopRunSuccess):
            self._handle_success(payload)
        else:
            self._handle_error(payload)

    def _handle_success(self, payload: DesktopRunSuccess) -> None:
        """Update result controls after a successful comparison."""

        self._last_outputs = payload.outputs
        counts: dict[str, int] = {}
        for change in payload.result.changes:
            counts[change.change_type] = counts.get(change.change_type, 0) + 1
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
        table_change_count = _reported_table_change_count(payload.outputs)
        table_note = (
            f"，表格变化 {table_change_count}"
            if table_change_count is not None
            else "；表格变化请查看报告"
        )
        self.summary_var.set(
            f"{assessment_note}："
            f"章节修改 {counts.get('modified', 0)}，"
            f"章节新增 {counts.get('added', 0)}，"
            f"章节删除 {counts.get('deleted', 0)}"
            f"{table_note}"
        )
        html_path = payload.outputs["html"]
        self.report_path_var.set(f"HTML 报告: {html_path}")
        self.status_var.set(status_text)
        self.open_html_button.configure(state="normal")
        self.open_dir_button.configure(state="normal")

    def _handle_error(self, payload: object) -> None:
        """Show a user-readable error from the background worker."""

        message = str(payload)
        self.summary_var.set("本次比较未生成报告。")
        self.report_path_var.set("")
        self.status_var.set("运行失败。")
        messagebox.showerror("运行失败", message)

    def _set_running(self, running: bool) -> None:
        """Toggle button and progress states while a comparison is active."""

        if running:
            self.run_button.configure(state="disabled")
            self.open_html_button.configure(state="disabled")
            self.open_dir_button.configure(state="disabled")
            self.root.configure(cursor="watch")
            self.progress.start(24)
        else:
            self.run_button.configure(state="normal")
            self.root.configure(cursor="")
            self.progress.stop()

    def open_html_report(self) -> None:
        """Open the latest HTML report in the user's default browser."""

        if not self._last_outputs:
            return
        webbrowser.open(self._last_outputs["html"].as_uri())

    def open_report_directory(self) -> None:
        """Open the latest report directory in the platform file manager."""

        if not self._last_outputs:
            return
        open_path(self._last_outputs["report_dir"])


def open_path(path: Path) -> None:
    """Open a file or directory with the current operating system shell."""

    resolved = str(path.resolve())
    if sys.platform == "darwin":
        subprocess.run(["open", resolved], check=False)
    elif os.name == "nt":
        os.startfile(resolved)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", resolved], check=False)


def run_smoke_test() -> None:
    """Instantiate the GUI without entering the main loop.

    Packaging tests use this to verify that all widgets can be created, styled,
    and validated in the bundled Python runtime without requiring a human to
    click through the application.
    """

    root = tk.Tk()
    root.geometry("980x700+0+0")  # 用接近真实首屏的窗口尺寸做控件输入检查。
    app = ProtocolDiffDesktopApp(root)
    assert root.title() == "协议 PDF 差异对比工具"
    assert app.output_dir_var.get()
    root.update_idletasks()  # 先让 Tk 完成布局，后续才能检查控件是否真正挂到 grid 上。
    assert app.run_button.cget("text") == "开始比较"  # 确认主按钮不是旧文案或旧界面。
    assert app.run_button.cget("command")  # 确认主按钮绑定了回调，而不是只有静态文字。
    widget_texts = collect_widget_texts(root)  # 收集所有可见控件文案，检查关键输入是否存在。
    required_labels = {
        "旧协议起始页",  # 旧 PDF 范围起点输入框必须可见。
        "旧协议终止页",  # 旧 PDF 范围终点输入框必须可见。
        "新协议起始页",  # 新 PDF 范围起点输入框必须可见。
        "新协议终止页",  # 新 PDF 范围终点输入框必须可见。
        "开始比较",  # 主运行按钮必须可见。
    }
    missing_labels = sorted(required_labels - widget_texts)  # 找出缺失控件，方便构建失败时定位。
    assert not missing_labels, f"桌面界面缺少关键控件: {', '.join(missing_labels)}"  # 缺控件时直接失败。
    assert len(app.file_browse_buttons) == 3  # 旧 PDF、新 PDF、输出目录都必须有选择按钮。
    for label, entry in app.page_entry_widgets.items():
        assert entry.winfo_class() == "Entry", f"{label} 不是输入框"  # 防止标签存在但输入框丢失。
        assert entry.winfo_manager() == "grid", f"{label} 未加入布局"  # 防止控件创建了但没有显示。
        assert str(entry.cget("state")) != "disabled", f"{label} 被禁用"  # 防止输入框看得见但用户不能编辑。
        entry.focus_force()  # 强制聚焦输入框，模拟用户点击后准备输入。
        entry.delete(0, tk.END)  # 清空输入框，模拟用户准备输入页码。
        entry.insert(0, "2")  # 用 Tk 的文本插入接口验证控件可写，避免 Windows runner 不派发按键字符。
        root.update_idletasks()  # 处理布局和控件状态更新，确认输入框值已经变化。
        assert entry.get() == "2", f"{label} 无法输入页码"  # 如果 Entry 被错误禁用，这里会暴露。
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

    root = tk.Tk()
    ProtocolDiffDesktopApp(root)
    root.mainloop()
    return 0
