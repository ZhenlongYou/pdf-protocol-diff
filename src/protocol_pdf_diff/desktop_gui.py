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
        self.root.minsize(900, 640)

        self.old_pdf_var = tk.StringVar()
        self.new_pdf_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=str(PROJECT_ROOT / "results"))
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

        self._configure_style()
        self._build_layout()

    def _configure_style(self) -> None:
        """Apply restrained desktop styling while keeping native controls."""

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
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

        settings_frame = ttk.LabelFrame(container, text="匹配设置", style="Section.TLabelframe")
        settings_frame.grid(row=4, column=0, sticky="ew", pady=(0, 14))
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

        action_frame = ttk.Frame(container)
        action_frame.grid(row=5, column=0, sticky="ew", pady=(0, 14))
        action_frame.columnconfigure(5, weight=1)
        self.run_button = ttk.Button(
            action_frame,
            text="运行比较",
            style="Primary.TButton",
            command=self.run_comparison,
        )
        self.run_button.grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Button(action_frame, text="运行 Demo", command=self.run_demo).grid(
            row=0, column=1, sticky="w", padx=(0, 10)
        )
        self.open_html_button = ttk.Button(
            action_frame,
            text="打开 HTML 报告",
            command=self.open_html_report,
            state="disabled",
        )
        self.open_html_button.grid(row=0, column=2, sticky="w", padx=(0, 10))
        self.open_dir_button = ttk.Button(
            action_frame,
            text="打开输出目录",
            command=self.open_report_directory,
            state="disabled",
        )
        self.open_dir_button.grid(row=0, column=3, sticky="w")

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
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, sticky="ew", padx=(0, 10), pady=8
        )
        ttk.Button(parent, text="选择", command=command).grid(
            row=row, column=2, sticky="e", padx=(0, 10), pady=8
        )

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
        ttk.Entry(parent, textvariable=start_var, width=10).grid(
            row=row, column=1, sticky="w", padx=(0, 18), pady=8
        )
        ttk.Label(parent, text=end_label).grid(row=row, column=2, sticky="w", padx=10, pady=8)
        ttk.Entry(parent, textvariable=end_var, width=10).grid(
            row=row, column=3, sticky="w", padx=(0, 18), pady=8
        )

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
            old_pdf, new_pdf = write_demo_pdfs(PROJECT_ROOT / "work" / "demo_inputs")
        except Exception as exc:  # pragma: no cover - defensive UI fallback.
            messagebox.showerror("Demo 生成失败", str(exc))
            return
        self.old_pdf_var.set(str(old_pdf))
        self.new_pdf_var.set(str(new_pdf))
        self.old_start_var.set("")
        self.old_end_var.set("")
        self.new_start_var.set("")
        self.new_end_var.set("")
        self.run_comparison()

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
        worker = threading.Thread(target=self._run_worker, args=(config,), daemon=True)
        worker.start()
        self.root.after(120, self._poll_result_queue)

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
            self.root.after(120, self._poll_result_queue)
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
            self.open_html_button.configure(state="disabled")
            self.open_dir_button.configure(state="disabled")
            self.progress.start(12)
        else:
            self.run_button.configure(state="normal")
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
    root.withdraw()
    app = ProtocolDiffDesktopApp(root)
    assert root.title() == "协议 PDF 差异对比工具"
    assert app.output_dir_var.get()
    root.update_idletasks()
    root.destroy()


def main() -> int:
    """Launch the desktop application."""

    root = tk.Tk()
    ProtocolDiffDesktopApp(root)
    root.mainloop()
    return 0
