from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from build_desktop import APP_NAME, PROJECT_ROOT, WEB_UI_DIR, build_command, expected_artifact
from protocol_pdf_diff import webview_gui
from protocol_pdf_diff.webview_gui import (
    ProtocolDiffJsApi,
    ProtocolDiffWebApi,
    load_web_ui,
    select_webview_backend,
    validate_renderer_probe,
    validate_web_ui_contract,
)


class WebviewGuiTests(unittest.TestCase):
    def test_shared_html_is_the_single_visual_contract_for_macos_and_windows(self) -> None:
        html = load_web_ui()

        self.assertIn("Protocol Comparison Tool", html)
        self.assertIn("旧版 PDF", html)
        self.assertIn("新版 PDF", html)
        self.assertIn("全部页面", html)
        self.assertIn("选择页面", html)
        self.assertIn("正在比较", html)
        self.assertIn("@keyframes run-slide", html)
        self.assertIn("backdrop-filter: blur", html)
        self.assertIn("radial-gradient", html)
        self.assertNotIn("交换旧/新", html)
        self.assertNotIn("尚未选择", html)
        self.assertNotIn("请选择两份 PDF", html)
        validate_web_ui_contract(html)

    def test_windows_forces_edgechromium_while_macos_uses_native_webkit(self) -> None:
        self.assertEqual("edgechromium", select_webview_backend("win32"))
        self.assertIsNone(select_webview_backend("darwin"))
        valid_probe = {
            "title": "Protocol Comparison Tool",
            "background": "radial-gradient(rgb(0, 0, 0), rgba(0, 0, 0, 0))",
            "animation": "run-slide",
            "reducedMotion": False,
            "columns": "520px 520px",
            "backdrop": "blur(18px)",
            "overflowFree": True,
            "webview2": True,
        }
        validate_renderer_probe(valid_probe, "win32")
        with self.assertRaisesRegex(RuntimeError, "WebView2"):
            validate_renderer_probe({**valid_probe, "webview2": False}, "win32")
        with self.assertRaisesRegex(RuntimeError, "双文档布局"):
            validate_renderer_probe({**valid_probe, "columns": "1040px"}, "darwin")
        with self.assertRaisesRegex(RuntimeError, "玻璃卡片"):
            validate_renderer_probe({**valid_probe, "backdrop": "none"}, "darwin")
        with self.assertRaisesRegex(RuntimeError, "横向溢出"):
            validate_renderer_probe({**valid_probe, "overflowFree": False}, "darwin")
        validate_renderer_probe(
            {**valid_probe, "animation": "none", "reducedMotion": True}, "darwin"
        )
        with self.assertRaisesRegex(RuntimeError, "减少动态效果"):
            validate_renderer_probe(
                {**valid_probe, "animation": "run-slide", "reducedMotion": True},
                "darwin",
            )

    def test_frozen_apps_package_the_same_web_ui_on_both_platforms(self) -> None:
        args = Namespace(clean=False, console=False, onefile=False)
        for platform_name in ("darwin", "win32"):
            command = build_command(args, platform_name=platform_name)
            data_index = command.index("--add-data")
            self.assertEqual(
                f"{WEB_UI_DIR}:protocol_pdf_diff/webui",
                command[data_index + 1],
            )

    def test_windows_artifact_paths_cover_onefile_and_onedir(self) -> None:
        with mock.patch("build_desktop.sys.platform", "win32"):
            self.assertEqual(
                PROJECT_ROOT / "dist" / f"{APP_NAME}.exe",
                expected_artifact(onefile=True),
            )
            self.assertEqual(
                PROJECT_ROOT / "dist" / APP_NAME / f"{APP_NAME}.exe",
                expected_artifact(onefile=False),
            )

    def test_api_builds_real_page_window_options_and_emits_safe_json_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = temp_path / "old.pdf"
            new_pdf = temp_path / "new.pdf"
            old_pdf.write_bytes(b"%PDF-1.4\n")
            new_pdf.write_bytes(b"%PDF-1.4\n")
            fake_window = mock.Mock()
            result = mock.Mock()
            result.assessment = None
            result.changes = []
            result.visual_review_items = []
            outputs = {
                "html": temp_path / "report.html",
                "report_dir": temp_path,
                "markdown": temp_path / "report.md",
            }
            outputs["markdown"].write_text("## 比较摘要\n", encoding="utf-8")

            def fake_run_diff(old: Path, new: Path, options: object, *, progress_observer: object) -> object:
                self.assertEqual(old_pdf.resolve(), old)
                self.assertEqual(new_pdf.resolve(), new)
                self.assertEqual(16, options.old_start_page)
                self.assertEqual(18, options.old_end_page)
                self.assertEqual(33, options.new_start_page)
                self.assertEqual(35, options.new_end_page)
                return result

            api = ProtocolDiffWebApi(
                run_diff_func=fake_run_diff,
                write_reports_func=lambda *_args, **_kwargs: outputs,
            )
            api.bind_window(fake_window)
            response = api.run_comparison_sync(
                {
                    "old_pdf": str(old_pdf),
                    "new_pdf": str(new_pdf),
                    "output_dir": str(temp_path),
                    "old_mode": "range",
                    "old_start": "16",
                    "old_end": "18",
                    "new_mode": "range",
                    "new_start": "33",
                    "new_end": "35",
                    "min_similarity": "0.72",
                    "max_snippets": "20",
                    "include_unchanged": False,
                    "auto_open": False,
                }
            )

            self.assertTrue(response["ok"], response)
            scripts = [call.args[0] for call in fake_window.evaluate_js.call_args_list]
            self.assertTrue(any("receive" in script for script in scripts))
            self.assertTrue(any('"stage": "report"' in script for script in scripts))
            for script in scripts:
                payload = script.removeprefix("window.protocolDiff.receive(").removesuffix(");")
                json.loads(payload)

    def test_ui_event_failure_cannot_change_a_successful_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            old_pdf = temp_path / "old.pdf"
            new_pdf = temp_path / "new.pdf"
            old_pdf.write_bytes(b"%PDF-1.4\n")
            new_pdf.write_bytes(b"%PDF-1.4\n")
            result = mock.Mock(assessment=None, changes=[], visual_review_items=[])
            report = temp_path / "report.html"
            report.write_text("ok", encoding="utf-8")
            api = ProtocolDiffWebApi(
                run_diff_func=lambda *_args, **_kwargs: result,
                write_reports_func=lambda *_args, **_kwargs: {
                    "html": report,
                    "report_dir": temp_path,
                },
            )
            broken_window = mock.Mock()
            broken_window.evaluate_js.side_effect = RuntimeError("window closed")
            api.bind_window(broken_window)

            response = api.run_comparison_sync(
                {
                    "old_pdf": str(old_pdf),
                    "new_pdf": str(new_pdf),
                    "output_dir": str(temp_path),
                    "old_mode": "all",
                    "new_mode": "all",
                    "min_similarity": "0.72",
                    "max_snippets": "20",
                }
            )

            self.assertTrue(response["ok"], response)

    def test_js_bridge_exposes_only_the_safe_async_surface(self) -> None:
        facade = ProtocolDiffJsApi(ProtocolDiffWebApi())
        exposed = {name for name in dir(facade) if not name.startswith("_")}
        self.assertEqual(
            {
                "choose_output_dir",
                "choose_pdf",
                "get_defaults",
                "open_html_report",
                "open_output_dir",
                "run_comparison",
            },
            exposed,
        )

    def test_thread_start_failure_restores_idle_state(self) -> None:
        api = ProtocolDiffWebApi()
        with mock.patch("protocol_pdf_diff.webview_gui.threading.Thread") as thread:
            thread.return_value.start.side_effect = RuntimeError("no threads")
            first = api.run_comparison({})
            second = api.run_comparison({})
        self.assertFalse(first["ok"])
        self.assertFalse(second["ok"])
        self.assertIn("无法启动比较任务", second["error"])

    def test_close_is_cancelled_only_while_a_run_is_active(self) -> None:
        api = ProtocolDiffWebApi()
        self.assertTrue(api._handle_close_request())
        api._running = True
        self.assertFalse(api._handle_close_request())
        api._running = False
        self.assertTrue(api._handle_close_request())

    def test_success_uses_reader_report_counts_not_raw_engine_counts(self) -> None:
        result = mock.Mock(assessment=None)
        result.changes = [
            *[mock.Mock(change_type="modified") for _ in range(33)],
            *[mock.Mock(change_type="added") for _ in range(2)],
            mock.Mock(change_type="deleted"),
        ]
        result.visual_review_items = [object()] * 9
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            markdown = folder / "report.md"
            markdown.write_text(
                "## 汇总\n"
                "| 项目 | 数量 |\n|---|---|\n"
                "| 核心技术变化 | 15 |\n"
                "| 章节修改 / 新增 / 删除 | 14 / 1 / 0 |\n"
                "| 变化表格 | 6 |\n"
                "| 视觉漏检核对项 | 4 |\n",
                encoding="utf-8",
            )
            payload = ProtocolDiffWebApi._success_payload(
                result,
                {"html": folder / "report.html", "markdown": markdown},
            )
        self.assertEqual(
            {"body": 15, "modified": 14, "added": 1, "deleted": 0, "table": 6, "visual": 4},
            payload["counts"],
        )

    def test_success_hides_counts_when_reader_summary_is_missing_or_malformed(self) -> None:
        result = mock.Mock(assessment=None, changes=[mock.Mock(change_type="modified")])
        result.visual_review_items = [object()]
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            markdown = folder / "report.md"
            markdown.write_text("## 汇总\n| 损坏 | 数据 |\n", encoding="utf-8")
            payload = ProtocolDiffWebApi._success_payload(
                result,
                {"html": folder / "report.html", "markdown": markdown},
            )
        self.assertIsNone(payload["counts"])
        self.assertIn("统计请查看报告", payload["status"])

    def test_success_hides_impossible_negative_reader_counts(self) -> None:
        result = mock.Mock(assessment=None, changes=[])
        result.visual_review_items = []
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            markdown = folder / "report.md"
            markdown.write_text(
                "## 汇总\n"
                "| 项目 | 数量 |\n|---|---|\n"
                "| 核心技术变化 | -5 |\n"
                "| 章节修改 / 新增 / 删除 | -7 / 1 / 1 |\n"
                "| 变化表格 | -3 |\n"
                "| 视觉漏检核对项 | -2 |\n",
                encoding="utf-8",
            )
            payload = ProtocolDiffWebApi._success_payload(
                result,
                {"html": folder / "report.html", "markdown": markdown},
            )
        self.assertIsNone(payload["counts"])

    def test_open_failures_always_include_a_visible_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            html = folder / "report.html"
            html.write_text("ok", encoding="utf-8")
            api = ProtocolDiffWebApi()
            api._last_outputs = {"html": html, "report_dir": folder}
            with mock.patch("protocol_pdf_diff.webview_gui.webbrowser.open", return_value=False):
                html_response = api.open_html_report()
            with mock.patch("protocol_pdf_diff.webview_gui.open_path", return_value=False):
                dir_response = api.open_output_dir()
        self.assertFalse(html_response["ok"])
        self.assertIn("error", html_response)
        self.assertFalse(dir_response["ok"])
        self.assertIn("error", dir_response)

    def test_window_registers_the_close_guard(self) -> None:
        handlers: list[object] = []

        class FakeEvent:
            def __iadd__(self, handler: object) -> "FakeEvent":
                handlers.append(handler)
                return self

        fake_window = mock.Mock()
        fake_window.events = SimpleNamespace(closing=FakeEvent())
        fake_webview = mock.Mock()
        fake_webview.create_window.return_value = fake_window
        with mock.patch.dict(sys.modules, {"webview": fake_webview}):
            window, controller = webview_gui.create_webview_window()
        self.assertIs(fake_window, window)
        self.assertEqual([controller._handle_close_request], handlers)

    def test_windows_missing_webview2_reports_actionable_native_error(self) -> None:
        fake_webview = mock.Mock()
        fake_webview.start.side_effect = RuntimeError("WebView2 runtime missing")
        fake_ctypes = mock.Mock()
        with (
            mock.patch.dict(sys.modules, {"webview": fake_webview, "ctypes": fake_ctypes}),
            mock.patch.object(webview_gui.sys, "platform", "win32"),
            mock.patch.object(
                webview_gui,
                "create_webview_window",
                return_value=(mock.Mock(), mock.Mock()),
            ),
        ):
            exit_code = webview_gui.main()

        self.assertEqual(2, exit_code)
        message_args = fake_ctypes.windll.user32.MessageBoxW.call_args.args
        self.assertIn("WebView2 Evergreen Runtime", message_args[1])
        self.assertEqual("Protocol Comparison Tool", message_args[2])

    def test_windows_workflow_requires_native_screenshot_and_probe(self) -> None:
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "build-desktop.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python build_desktop.py --clean", workflow)
        self.assertNotIn("python build_desktop.py --clean --onefile", workflow)
        self.assertIn("dist/ProtocolPdfDiff/ProtocolPdfDiff.exe", workflow)
        self.assertIn("capture_windows_ui.ps1", workflow)
        self.assertIn("windows-webview2.png", workflow)
        self.assertIn("windows-renderer-probe.json", workflow)
        capture_script = (PROJECT_ROOT / "scripts" / "capture_windows_ui.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("PROTOCOL_DIFF_RENDERER_PROBE_PATH", capture_script)
        self.assertIn("ConvertFrom-Json", capture_script)
        self.assertIn("Timed out waiting for the WebView2 renderer probe", capture_script)

    def test_renderer_probe_is_published_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "probe.json"
            fake_window = mock.Mock()
            fake_window.events.loaded.wait.return_value = True
            fake_window.evaluate_js.return_value = {
                "title": "Protocol Comparison Tool",
                "background": "radial-gradient(red, blue)",
                "animation": "run-slide",
                "reducedMotion": False,
                "columns": "500px 500px",
                "backdrop": "blur(18px)",
                "overflowFree": True,
                "webview2": True,
            }
            with mock.patch("protocol_pdf_diff.webview_gui.os.replace", wraps=webview_gui.os.replace) as replace:
                webview_gui._write_renderer_probe(fake_window, str(target))
            replace.assert_called_once()
            temporary, published = replace.call_args.args
            self.assertEqual(target.resolve(), published)
            self.assertEqual(target.resolve().parent, temporary.parent)
            self.assertFalse(temporary.exists())
            self.assertEqual("Protocol Comparison Tool", json.loads(target.read_text())["title"])


if __name__ == "__main__":
    unittest.main()
