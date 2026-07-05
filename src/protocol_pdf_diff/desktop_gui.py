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
from .reporting import write_reports
from .sample_data import write_demo_pdfs


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def default_output_dir() -> Path:
    """Return a user-writable report folder outside the packaged app bundle."""

    # 把默认报告目录放在用户文档目录，避免打包后的 app 尝试写入只读 bundle。
    return Path.home() / "Documents" / "ProtocolPdfDiffReports"


def default_demo_dir() -> Path:
    """Return the folder used for GUI demo PDFs."""

    # Demo PDF 也放到用户文档目录下，避免冻结 app 在自身 bundle 附近写文件失败。
    return default_output_dir() / "_demo_inputs"


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
    if number <= 0:
        raise ValueError(f"{label} 必须大于 0。")
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
        self.unchanged_similarity_var = tk.StringVar(value="0.985")
        self.max_snippets_var = tk.StringVar(value="20")
        self.include_unchanged_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="请选择新旧 PDF，或运行内置 demo。")
        self.summary_var = tk.StringVar(value="尚未生成报告")
        self.report_path_var = tk.StringVar(value="")

        self._last_outputs: dict[str, Path] | None = None
        self._result_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.page_entry_widgets: dict[str, tk.Entry] = {}  # 保存四个原生页码输入框，供 smoke test 检查真实输入能力。
        self.file_browse_buttons: list[ttk.Button] = []  # 保存三个“选择”按钮，供打包后自测确认按钮存在。
        self.demo_button: ttk.Button | None = None  # 记录 Demo 按钮，运行比较时临时禁用，避免重复触发。

        self._configure_style()
        self._build_layout()

    def _configure_style(self) -> None:
        """Apply restrained desktop styling while keeping native controls."""

        style = ttk.Style(self.root)
        if sys.platform != "darwin" and "clam" in style.theme_names():
            # macOS 原生 Aqua 主题的输入焦点更稳；其它系统才使用 clam 统一观感。
            style.theme_use("clam")
        style.configure(".", font=("Arial", 12))
        style.configure("Title.TLabel", font=("Arial", 20, "bold"))
        style.configure("Section.TLabelframe.Label", font=("Arial", 12, "bold"))
        style.configure("Primary.TButton", font=("Arial", 12, "bold"))
        style.configure("Status.TLabel", foreground="#415166")

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

        files_frame = ttk.LabelFrame(container, text="PDF 文件", style="Section.TLabelframe")
        files_frame.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        files_frame.columnconfigure(1, weight=1)
        self._add_file_row(files_frame, 0, "旧协议", self.old_pdf_var, self._browse_old_pdf)
        self._add_file_row(files_frame, 1, "新协议", self.new_pdf_var, self._browse_new_pdf)
        self._add_file_row(files_frame, 2, "输出目录", self.output_dir_var, self._browse_output_dir)

        ranges_frame = ttk.LabelFrame(container, text="页码范围", style="Section.TLabelframe")
        ranges_frame.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        for column in range(4):
            ranges_frame.columnconfigure(column, weight=1)
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
        action_frame = ttk.LabelFrame(container, text="开始生成报告", style="Section.TLabelframe")
        action_frame.grid(row=4, column=0, sticky="ew", pady=(0, 14))  # 操作区紧跟页码范围。
        action_frame.columnconfigure(4, weight=1)  # 右侧留出弹性空间，避免按钮挤压。
        self.run_button = ttk.Button(
            action_frame,  # 按钮放在“开始生成报告”区域内。
            text="开始比较 / 生成报告",  # 文案同时说明点击后会生成报告。
            style="Primary.TButton",  # 使用主按钮样式突出最常用操作。
            command=self.run_comparison,  # 点击后进入输入校验和后台比较流程。
        )
        self.run_button.grid(row=0, column=0, sticky="w", padx=10, pady=12)  # 固定在操作区最左侧。
        self.demo_button = ttk.Button(action_frame, text="填入 Demo 文件", command=self.run_demo)
        self.demo_button.grid(
            row=0, column=1, sticky="w", padx=(0, 10), pady=12  # Demo 按钮紧跟主按钮，方便自测。
        )
        self.open_html_button = ttk.Button(
            action_frame,  # 报告按钮也放在同一操作区。
            text="打开 HTML 报告",  # 运行成功后直接打开最直观的 HTML 报告。
            command=self.open_html_report,  # 点击后用默认浏览器打开最近一次 HTML。
            state="disabled",  # 未生成报告前禁用，避免用户打开空路径。
        )
        self.open_html_button.grid(row=0, column=2, sticky="w", padx=(0, 10), pady=12)  # 与主按钮同一行。
        self.open_dir_button = ttk.Button(
            action_frame,  # 输出目录按钮放在报告按钮后面。
            text="打开输出目录",  # 方便用户查看 TXT/CSV/JSON 等其它文件。
            command=self.open_report_directory,  # 点击后打开最近一次报告目录。
            state="disabled",  # 未生成报告前禁用，避免打开无效目录。
        )
        self.open_dir_button.grid(row=0, column=3, sticky="w", pady=12)  # 保持操作区按钮横向排列。

        settings_frame = ttk.LabelFrame(container, text="匹配设置", style="Section.TLabelframe")
        settings_frame.grid(row=5, column=0, sticky="ew", pady=(0, 14))  # 高级参数放在主操作区之后。
        for column in range(8):
            settings_frame.columnconfigure(column, weight=1)
        self._add_setting_entry(settings_frame, 0, 0, "章节匹配阈值", self.min_similarity_var)
        self._add_setting_entry(settings_frame, 0, 2, "未变化阈值", self.unchanged_similarity_var)
        self._add_setting_entry(settings_frame, 0, 4, "最大片段数", self.max_snippets_var)
        ttk.Checkbutton(
            settings_frame,
            text="列出未变化章节",
            variable=self.include_unchanged_var,
        ).grid(row=0, column=6, columnspan=2, sticky="w", padx=8, pady=10)

        self.progress = ttk.Progressbar(container, mode="indeterminate")
        self.progress.grid(row=6, column=0, sticky="ew")
        ttk.Label(container, textvariable=self.status_var, style="Status.TLabel").grid(
            row=7, column=0, sticky="w", pady=(10, 4)
        )

        result_frame = ttk.LabelFrame(container, text="结果", style="Section.TLabelframe")
        result_frame.grid(row=8, column=0, sticky="nsew", pady=(10, 0))
        container.rowconfigure(8, weight=1)
        result_frame.columnconfigure(0, weight=1)
        ttk.Label(result_frame, textvariable=self.summary_var, wraplength=820).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 6)
        )
        ttk.Label(result_frame, textvariable=self.report_path_var, wraplength=820).grid(
            row=1, column=0, sticky="w", padx=12, pady=(0, 12)
        )

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
        browse_button = ttk.Button(parent, text="选择", command=command)  # “选择”按钮打开文件或目录选择器。
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

        ttk.Label(parent, text=start_label).grid(row=row, column=0, sticky="w", padx=10, pady=8)
        start_entry = self._create_page_entry(parent, start_var)  # 起始页输入框，留空表示默认起点。
        start_entry.grid(
            row=row, column=1, sticky="w", padx=(0, 18), pady=8, ipady=4  # 加高输入框，点击区域更明确。
        )
        ttk.Label(parent, text=end_label).grid(row=row, column=2, sticky="w", padx=10, pady=8)
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
            highlightbackground="#8ea0b8",  # 未聚焦时使用柔和灰蓝边框。
            highlightcolor="#2563eb",  # 聚焦时使用蓝色边框提示可输入。
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
            unchanged_similarity=parse_positive_float(
                self.unchanged_similarity_var.get(), "未变化阈值"
            ),
            max_snippets_per_section=parse_positive_int(
                self.max_snippets_var.get(), "最大片段数"
            ),
            include_unchanged_sections=self.include_unchanged_var.get(),
            old_start_page=parse_optional_page(self.old_start_var.get(), "旧协议起始页"),
            old_end_page=parse_optional_page(self.old_end_var.get(), "旧协议终止页"),
            new_start_page=parse_optional_page(self.new_start_var.get(), "新协议起始页"),
            new_end_page=parse_optional_page(self.new_end_var.get(), "新协议终止页"),
        )
        return DesktopRunConfig(old_pdf=old_pdf, new_pdf=new_pdf, output_dir=output_dir, options=options)

    def run_demo(self) -> None:
        """Generate demo PDFs and run the GUI workflow with those inputs."""

        try:
            old_pdf, new_pdf = write_demo_pdfs(default_demo_dir())
        except Exception as exc:  # pragma: no cover - defensive UI fallback.
            messagebox.showerror("Demo 生成失败", str(exc))
            return
        self.old_pdf_var.set(str(old_pdf))
        self.new_pdf_var.set(str(new_pdf))
        self.output_dir_var.set(str(default_output_dir()))
        self.old_start_var.set("")
        self.old_end_var.set("")
        self.new_start_var.set("")
        self.new_end_var.set("")
        self.summary_var.set("Demo 文件已填入")
        self.report_path_var.set("")
        self.status_var.set("Demo 文件已填入；可先填写页码范围，再点击开始比较。")

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
        warning_note = f"；警告 {len(payload.result.warnings)} 条" if payload.result.warnings else ""
        self.summary_var.set(
            "完成："
            f"修改 {counts.get('modified', 0)}，"
            f"新增 {counts.get('added', 0)}，"
            f"删除 {counts.get('deleted', 0)}"
            f"{warning_note}"
        )
        html_path = payload.outputs["html"]
        self.report_path_var.set(f"HTML 报告: {html_path}")
        self.status_var.set("比较完成。")
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
            if self.demo_button is not None:
                self.demo_button.configure(state="disabled")
            self.open_html_button.configure(state="disabled")
            self.open_dir_button.configure(state="disabled")
            self.root.configure(cursor="watch")
            self.progress.start(24)
        else:
            self.run_button.configure(state="normal")
            if self.demo_button is not None:
                self.demo_button.configure(state="normal")
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
    assert app.run_button.cget("text") == "开始比较 / 生成报告"  # 确认主按钮不是旧文案或旧界面。
    assert app.run_button.cget("command")  # 确认主按钮绑定了回调，而不是只有静态文字。
    widget_texts = collect_widget_texts(root)  # 收集所有可见控件文案，检查关键输入是否存在。
    required_labels = {
        "旧协议起始页",  # 旧 PDF 范围起点输入框必须可见。
        "旧协议终止页",  # 旧 PDF 范围终点输入框必须可见。
        "新协议起始页",  # 新 PDF 范围起点输入框必须可见。
        "新协议终止页",  # 新 PDF 范围终点输入框必须可见。
        "开始比较 / 生成报告",  # 主运行按钮必须可见。
    }
    missing_labels = sorted(required_labels - widget_texts)  # 找出缺失控件，方便构建失败时定位。
    assert not missing_labels, f"桌面界面缺少关键控件: {', '.join(missing_labels)}"  # 缺控件时直接失败。
    assert len(app.file_browse_buttons) == 3  # 旧 PDF、新 PDF、输出目录都必须有选择按钮。
    for label, entry in app.page_entry_widgets.items():
        assert entry.winfo_class() == "Entry", f"{label} 不是输入框"  # 防止标签存在但输入框丢失。
        assert entry.winfo_manager() == "grid", f"{label} 未加入布局"  # 防止控件创建了但没有显示。
        entry.focus_force()  # 强制聚焦输入框，模拟用户点击后准备输入。
        entry.delete(0, tk.END)  # 清空输入框，模拟用户准备输入页码。
        entry.event_generate("<KeyPress-2>")  # 通过键盘事件输入数字，覆盖“只能程序写值”的假通过。
        entry.event_generate("<KeyRelease-2>")  # 释放按键事件让 Tk 完成输入状态更新。
        root.update()  # 处理键盘事件，确认输入框值已经变化。
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
