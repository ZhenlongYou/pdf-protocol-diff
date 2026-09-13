"""Shared HTML desktop shell for macOS WKWebView and Windows WebView2.

pywebview uses the operating system web engine on both supported desktop
platforms.  The comparison pipeline remains ordinary Python; this module only
translates UI commands and immutable progress events across the JS bridge.

Framework references:
- https://pywebview.flowrl.com/guide/usage.html
- https://pywebview.flowrl.com/guide/interdomain.html
- https://pywebview.flowrl.com/guide/web_engine.html
"""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
import webbrowser
from collections.abc import Callable, Mapping
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .comparison_job import ComparisonCancelled, run_isolated_comparison
from .progress import ProgressEvent
from .ui_shared import (
    default_output_dir,
    open_path,
    page_range_values,
    parse_positive_float,
    parse_positive_int,
    reported_reader_summary,
)

if TYPE_CHECKING:
    from .models import DiffOptions, DiffResult


WEB_UI_RESOURCE = "webui/index.html"
REQUIRED_UI_TOKENS = (
    "Protocol Comparison Tool",
    "旧版 PDF",
    "新版 PDF",
    "全部页面",
    "选择页面",
    "正在比较",
    "@keyframes run-slide",
    "backdrop-filter: blur",
    "radial-gradient",
)
FORBIDDEN_UI_TOKENS = ("交换旧/新", "尚未选择", "请选择两份 PDF")
RENDERER_PROBE_SCRIPT = """
(() => {
  const cardStyle = getComputedStyle(document.querySelector('.card'));
  const stageStyle = getComputedStyle(document.querySelector('.stage'));
  const barStyle = getComputedStyle(document.querySelector('.run-track i'));
  const appStyle = getComputedStyle(document.querySelector('.app'));
  return {
    title: document.title,
    columns: stageStyle.gridTemplateColumns,
    backdrop: cardStyle.backdropFilter || cardStyle.webkitBackdropFilter || '',
    background: appStyle.backgroundImage,
    animation: barStyle.animationName,
    reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
    overflowFree: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    webview2: Boolean(window.chrome && window.chrome.webview)
  };
})()
"""


def load_web_ui() -> str:
    """Load the single packaged HTML/CSS/JS visual contract."""

    return files("protocol_pdf_diff").joinpath(WEB_UI_RESOURCE).read_text(
        encoding="utf-8"
    )


def validate_web_ui_contract(html: str | None = None) -> None:
    """Fail early if packaging or a later edit drops an approved UI element."""

    source = html if html is not None else load_web_ui()
    missing = [token for token in REQUIRED_UI_TOKENS if token not in source]
    forbidden = [token for token in FORBIDDEN_UI_TOKENS if token in source]
    if missing or forbidden:
        details = []
        if missing:
            details.append("缺少: " + ", ".join(missing))
        if forbidden:
            details.append("出现禁用文案: " + ", ".join(forbidden))
        raise RuntimeError("Web UI 合同无效（" + "；".join(details) + "）")


def select_webview_backend(platform_name: str | None = None) -> str | None:
    """Force modern Edge on Windows; macOS uses the OS-bundled WKWebView."""

    platform = platform_name or sys.platform
    return "edgechromium" if platform.startswith("win") else None


def validate_renderer_probe(
    probe: Mapping[str, object], platform_name: str | None = None
) -> None:
    """Verify the real native renderer kept the approved visual primitives."""

    platform = platform_name or sys.platform
    if probe.get("title") != "Protocol Comparison Tool":
        raise RuntimeError("桌面界面标题未正确加载。")
    if "gradient" not in str(probe.get("background", "")):
        raise RuntimeError("桌面背景样式未正确渲染。")
    animation = probe.get("animation")
    if bool(probe.get("reducedMotion")):
        if animation != "none":
            raise RuntimeError("减少动态效果设置未被桌面界面遵循。")
    elif animation != "run-slide":
        raise RuntimeError("运行状态动画未正确加载。")
    columns = str(probe.get("columns", "")).split()
    if len(columns) != 2:
        raise RuntimeError("双文档布局未正确渲染。")
    if str(probe.get("backdrop", "")) in {"", "none"}:
        raise RuntimeError("玻璃卡片效果未正确渲染。")
    if not bool(probe.get("overflowFree")):
        raise RuntimeError("桌面界面出现横向溢出。")
    if platform.startswith("win") and not bool(probe.get("webview2")):
        raise RuntimeError("Windows 未使用 Edge WebView2 渲染器。")


class ProtocolDiffWebApi:
    """Thread-safe Python API exposed to the local trusted HTML document."""

    def __init__(
        self,
        *,
        run_diff_func: Callable[..., Any] | None = None,
        write_reports_func: Callable[..., dict[str, Path]] | None = None,
    ) -> None:
        self._run_diff = run_diff_func
        self._write_reports = write_reports_func
        self._window: Any | None = None
        self._run_lock = threading.Lock()
        self._running = False
        self._last_outputs: dict[str, Path] = {}
        self._cancel_event = threading.Event()
        self._run_state: dict[str, object] = {"type": "idle", "revision": 0}
        self._revision = 0
        self._published = False
        self._close_requested = False

    def bind_window(self, window: Any) -> None:
        self._window = window

    def get_defaults(self) -> dict[str, object]:
        return {
            "output_dir": str(default_output_dir()),
            "min_similarity": "0.72",
            "max_snippets": "20",
            "include_unchanged": False,
            "auto_open": False,
        }

    def choose_pdf(self, side: str) -> dict[str, object]:
        """Open the native file picker and return one PDF selection."""

        if side not in {"old", "new"}:
            return {"ok": False, "error": "文档位置无效。"}
        if self._window is None:
            return {"ok": False, "error": "窗口尚未准备好。"}
        import webview

        selected = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            allow_multiple=False,
            file_types=("PDF files (*.pdf)",),
        )
        if not selected:
            return {"ok": True, "cancelled": True}
        path = Path(selected[0]).expanduser().resolve()
        return {"ok": True, "path": str(path), "name": path.name}

    def choose_output_dir(self) -> dict[str, object]:
        if self._window is None:
            return {"ok": False, "error": "窗口尚未准备好。"}
        import webview

        selected = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not selected:
            return {"ok": True, "cancelled": True}
        return {"ok": True, "path": str(Path(selected[0]).expanduser().resolve())}

    def run_comparison(self, config: Mapping[str, object]) -> dict[str, object]:
        """Start one comparison without blocking the JS bridge thread."""

        with self._run_lock:
            if self._running:
                return {"ok": False, "error": "比较正在运行。"}
            self._running = True
            self._cancel_event = threading.Event()
            self._published = False
            self._close_requested = False
            self._record_state_locked({"type": "running", "stage": "starting"})
        worker = threading.Thread(
            target=self._run_worker,
            args=(dict(config),),
            name="protocol-diff-web-worker",
            daemon=False,
        )
        try:
            worker.start()
        except RuntimeError as exc:
            with self._run_lock:
                self._running = False
                self._record_state_locked({"type": "error", "message": f"无法启动比较任务：{exc}"})
            return {"ok": False, "error": f"无法启动比较任务：{exc}"}
        return {"ok": True, "started": True}

    def run_comparison_sync(self, config: Mapping[str, object]) -> dict[str, object]:
        """Execute one run synchronously for tests and the background worker."""

        try:
            old_pdf, new_pdf, output_dir, options = self._collect_config(config)
            self._emit({"type": "running", "stage": "read_old"})
            runner = self._run_diff
            if runner is None:
                from .table_view_transaction import run_diff_transaction as runner

            result = runner(
                old_pdf,
                new_pdf,
                options,
                progress_observer=self._emit_progress,
            )
            self._emit_progress(ProgressEvent(stage="report"))
            writer = self._write_reports
            if writer is None:
                from .reporting import write_reports as writer

            from .table_view_transaction import report_outcome
            outcome = report_outcome(result, output_dir, options, writer=writer)
            result, outputs = outcome.selected_result, outcome.outputs
            self._last_outputs = {key: Path(value) for key, value in outputs.items()}
            payload = self._success_payload(result, self._last_outputs)
            if bool(config.get("auto_open")):
                opened = self.open_html_report()
                if not opened.get("ok"):
                    payload["notice"] = opened.get("error", "无法自动打开 HTML 报告。")
            self._emit(payload)
            return {"ok": True, **payload}
        # JS bridge methods must convert every worker failure into a visible UI
        # error; otherwise pywebview returns a rejected promise with no recovery.
        except Exception as exc:  # noqa: BLE001
            payload = {"type": "error", "message": str(exc) or exc.__class__.__name__}
            self._emit(payload)
            return {"ok": False, "error": payload["message"]}

    def open_html_report(self) -> dict[str, object]:
        path = self._last_outputs.get("html")
        if path is None:
            return {"ok": False, "error": "尚未生成 HTML 报告。"}
        try:
            opened = webbrowser.open(path.expanduser().resolve().as_uri())
        except (OSError, ValueError, webbrowser.Error) as exc:
            return {"ok": False, "error": str(exc)}
        if not opened:
            return {
                "ok": False,
                "error": "系统未能打开 HTML 报告，请检查默认浏览器设置。",
                "path": str(path),
            }
        return {"ok": True, "path": str(path)}

    def open_output_dir(self) -> dict[str, object]:
        path = self._last_outputs.get("report_dir")
        if path is None:
            return {"ok": False, "error": "尚未生成输出目录。"}
        try:
            opened = open_path(path.expanduser().resolve())
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        if not opened:
            return {
                "ok": False,
                "error": "系统未能打开输出目录，请检查目录是否仍然存在。",
                "path": str(path),
            }
        return {"ok": True, "path": str(path)}

    def get_run_state(self) -> dict[str, object]:
        """Polling keeps slow WebView evaluation away from process cleanup."""
        with self._run_lock:
            return dict(self._run_state)

    def cancel_comparison(self) -> dict[str, object]:
        with self._run_lock:
            if not self._running:
                return {"ok": True, "cancelled": False}
            if self._published:
                return {"ok": False, "error": "报告已完成，正在恢复界面。"}
            self._cancel_event.set()
            self._record_state_locked({"type": "cancelling", "message": "正在停止并清理临时文件…"})
        return {"ok": True, "requested": True}

    def _record_state_locked(self, payload):
        self._revision += 1
        self._run_state = {**payload, "revision": self._revision}

    def _publish_progress(self, payload):
        with self._run_lock:
            if payload.get("type") == "cleanup_error" or not self._cancel_event.is_set():
                self._record_state_locked(payload)

    def _publish_report(self, outputs, payload):
        source = outputs["report_dir"]
        # The staging root and destination share a filesystem. The unique name
        # avoids same-second collisions; pre-existing reports are never reused.
        destination = source.parent.parent / (source.name + "_" + uuid.uuid4().hex)
        with self._run_lock:
            if self._cancel_event.is_set():
                raise ComparisonCancelled()
            if destination.exists():
                raise FileExistsError(f"报告目录已存在：{destination}")
            source.rename(destination)
            published = {key: destination / path.relative_to(source) for key, path in outputs.items()}
            self._last_outputs = published
            self._published = True
        return {**payload, "html_path": str(published["html"]), "report_dir": str(destination)}

    def _run_worker(self, config: dict[str, object]) -> None:
        payload = {"type": "error", "message": "比较任务意外结束。"}
        try:
            old_pdf, new_pdf, output_dir, options = self._collect_config(config)
            payload = run_isolated_comparison(
                old_pdf, new_pdf, options, output_dir, self._cancel_event,
                self._publish_progress, self._publish_report,
            )
        except ComparisonCancelled:
            payload = {"type": "cancelled", "message": "已取消，可重新开始比较。"}
        except Exception as exc:  # Every terminal failure must restore the form.
            payload = {"type": "error", "message": str(exc) or type(exc).__name__}
        finally:
            with self._run_lock:
                self._running = False
                self._record_state_locked(payload)
                close_requested = self._close_requested
                terminal_revision = self._revision
        if close_requested and self._window is not None:
            # Cleanup is already finished; closing cannot strand a worker.
            self._window.destroy()
        elif payload["type"] == "success" and bool(config.get("auto_open")):
            # Capture this run's path and revision before the next run can
            # replace last_outputs. A slow browser must not overwrite its state.
            notice = None
            try:
                if not webbrowser.open(Path(payload["html_path"]).resolve().as_uri()):
                    notice = "系统未能打开 HTML 报告，请检查默认浏览器设置。"
            except (OSError, ValueError, webbrowser.Error) as exc:
                notice = str(exc)
            if notice:
                with self._run_lock:
                    if self._revision == terminal_revision:
                        self._record_state_locked({**payload, "notice": notice})

    def _handle_close_request(self) -> bool:
        """A close during work requests cancellation, then closes after cleanup."""
        with self._run_lock:
            if not self._running:
                return True
            self._close_requested = True
        self.cancel_comparison()
        return False

    def _collect_config(
        self, config: Mapping[str, object]
    ) -> tuple[Path, Path, Path, DiffOptions]:
        from .models import DiffOptions

        old_pdf = Path(str(config.get("old_pdf", ""))).expanduser()
        new_pdf = Path(str(config.get("new_pdf", ""))).expanduser()
        if not old_pdf.is_file():
            raise ValueError("请选择有效的旧版 PDF。")
        if not new_pdf.is_file():
            raise ValueError("请选择有效的新版 PDF。")
        output_text = str(config.get("output_dir", "")).strip()
        if not output_text:
            raise ValueError("输出目录不能为空。")
        output_dir = Path(output_text).expanduser().resolve()
        old_start, old_end = page_range_values(
            str(config.get("old_mode", "all")),
            str(config.get("old_start", "")),
            str(config.get("old_end", "")),
            "旧版",
        )
        new_start, new_end = page_range_values(
            str(config.get("new_mode", "all")),
            str(config.get("new_start", "")),
            str(config.get("new_end", "")),
            "新版",
        )
        options = DiffOptions(
            min_section_match_similarity=parse_positive_float(
                str(config.get("min_similarity", "0.72")), "章节匹配阈值"
            ),
            max_snippets_per_section=parse_positive_int(
                str(config.get("max_snippets", "20")), "每章片段数"
            ),
            include_unchanged_sections=bool(config.get("include_unchanged", False)),
            old_start_page=old_start,
            old_end_page=old_end,
            new_start_page=new_start,
            new_end_page=new_end,
        )
        return old_pdf.resolve(), new_pdf.resolve(), output_dir, options

    def _emit_progress(self, event: ProgressEvent) -> None:
        self._emit(
            {
                "type": "progress",
                "stage": event.stage,
                "side": event.side,
                "completed_pages": event.completed_pages,
                "total_pages": event.total_pages,
                "unit": event.unit,
                "detail": event.detail,
            }
        )

    def _emit(self, payload: Mapping[str, object]) -> None:
        if self._window is None:
            return
        serialized = json.dumps(dict(payload), ensure_ascii=False)
        try:
            self._window.evaluate_js(f"window.protocolDiff.receive({serialized});")
        except Exception:  # noqa: BLE001 - UI observation must never alter diff output.
            return

    @staticmethod
    def _success_payload(
        result: DiffResult, outputs: Mapping[str, Path]
    ) -> dict[str, object]:
        assessment = result.assessment
        if assessment is None:
            reliability = "无法判断"
            status = "报告已生成，请人工复核。"
        elif assessment.state == "reliable":
            reliability = "可靠"
            status = "比较完成。"
        else:
            reliability = "需人工复核"
            status = "报告已生成，识别存在风险。"
        reader = reported_reader_summary(outputs)
        if reader is not None:
            counts: dict[str, int] | None = {
                "body": reader.body_changes,
                "modified": reader.modified,
                "added": reader.added,
                "deleted": reader.deleted,
                "table": reader.table_changes,
                "visual": reader.visual_items,
            }
        else:
            counts = None
            status += " 统计请查看报告。"
        return {
            "type": "success",
            "status": status,
            "reliability": reliability,
            "counts": counts,
            "html_path": str(outputs["html"]),
            "report_dir": str(outputs.get("report_dir", outputs["html"].parent)),
        }


class ProtocolDiffJsApi:
    """Minimal allow-list exposed to untrusted JavaScript calls."""

    def __init__(self, controller: ProtocolDiffWebApi) -> None:
        self._controller = controller

    def get_defaults(self) -> dict[str, object]:
        return self._controller.get_defaults()

    def choose_pdf(self, side: str) -> dict[str, object]:
        return self._controller.choose_pdf(side)

    def choose_output_dir(self) -> dict[str, object]:
        return self._controller.choose_output_dir()

    def run_comparison(self, config: Mapping[str, object]) -> dict[str, object]:
        return self._controller.run_comparison(config)

    def cancel_comparison(self) -> dict[str, object]:
        return self._controller.cancel_comparison()

    def get_run_state(self) -> dict[str, object]:
        return self._controller.get_run_state()

    def open_html_report(self) -> dict[str, object]:
        return self._controller.open_html_report()

    def open_output_dir(self) -> dict[str, object]:
        return self._controller.open_output_dir()


def create_webview_window(api: ProtocolDiffWebApi | None = None) -> tuple[Any, ProtocolDiffWebApi]:
    """Create the native window from the same HTML on every platform."""

    import webview

    html = load_web_ui()
    validate_web_ui_contract(html)
    bridge = api or ProtocolDiffWebApi()
    window = webview.create_window(
        "Protocol Comparison Tool",
        html=html,
        js_api=ProtocolDiffJsApi(bridge),
        width=1180,
        height=720,
        min_size=(760, 520),
        background_color="#18192D",
        text_select=True,
        confirm_close=False,
    )
    if window is None:
        raise RuntimeError("无法创建桌面窗口。")
    bridge.bind_window(window)
    window.events.closing += bridge._handle_close_request
    return window, bridge


def run_smoke_test(*, real_window: bool = False) -> None:
    """Validate packaged resources and optionally instantiate the native engine."""

    html = load_web_ui()
    validate_web_ui_contract(html)
    import webview

    if not hasattr(webview, "create_window") or not hasattr(webview, "start"):
        raise RuntimeError("pywebview 安装不完整。")
    if not real_window:
        return
    window, _api = create_webview_window()

    errors: list[Exception] = []

    def verify_after_load() -> None:
        try:
            _write_renderer_probe(window)
        except Exception as exc:  # noqa: BLE001 - re-raised after the GUI loop exits.
            errors.append(exc)
        finally:
            window.destroy()

    webview.start(
        verify_after_load,
        gui=select_webview_backend(),
        private_mode=True,
    )
    if errors:
        raise errors[0]


def _write_renderer_probe(window: Any, evidence_path: str | None = None) -> dict[str, object]:
    """Wait for the real DOM, validate it, and optionally write a ready marker."""

    if not window.events.loaded.wait(15):
        raise RuntimeError("桌面界面加载超时。")
    probe = window.evaluate_js(RENDERER_PROBE_SCRIPT)
    validate_renderer_probe(probe)
    path_text = evidence_path or os.environ.get("PROTOCOL_DIFF_RENDERER_PROBE_PATH")
    if path_text:
        target = Path(path_text).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(probe, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    return dict(probe)


def main() -> int:
    """Launch WKWebView on macOS or force EdgeChromium on Windows."""

    import webview

    window, _api = create_webview_window()
    try:
        evidence_path = os.environ.get("PROTOCOL_DIFF_RENDERER_PROBE_PATH")
        if evidence_path:
            webview.start(
                lambda: _write_renderer_probe(window, evidence_path),
                gui=select_webview_backend(),
                private_mode=True,
            )
        else:
            webview.start(gui=select_webview_backend(), private_mode=True)
    except Exception:  # Backend startup errors vary by OS/runtime.
        if sys.platform.startswith("win"):
            message = (
                "无法启动 Edge WebView2。\n\n"
                "请安装 Microsoft Edge WebView2 Evergreen Runtime 后重新打开。\n"
                "https://developer.microsoft.com/microsoft-edge/webview2/"
            )
            try:
                import ctypes

                ctypes.windll.user32.MessageBoxW(0, message, "Protocol Comparison Tool", 0x10)
            except (AttributeError, OSError):
                print(message, file=sys.stderr)
            return 2
        raise
    return 0
