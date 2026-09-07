"""Exercise real spawn, report publication, cancellation and OCR descendants."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from protocol_pdf_diff import comparison_job

from protocol_pdf_diff.comparison_job import ComparisonCancelled, run_isolated_comparison
from protocol_pdf_diff.models import DiffOptions
from protocol_pdf_diff.sample_data import write_demo_pdfs
from protocol_pdf_diff.webview_gui import ProtocolDiffWebApi


def _child_with_descendant(connection, old_pdf, new_pdf, options, staging):
    """A real long-lived OCR-like child inherits the worker's containment."""
    if os.name != "nt":
        os.setsid()
    connection.send({"type": "ready"})
    if connection.recv() != "go":
        return
    Path(staging, "partial.html").write_text("incomplete")
    script = Path(staging, "sleeper.py")
    script.write_text("import time\ntime.sleep(60)\n")
    child = subprocess.Popen([sys.executable, str(script)])
    connection.send({"type": "progress", "stage": "report", "pid": child.pid})
    try:
        time.sleep(60)
    finally:
        child.terminate()
        child.wait()


def _child_crashes(connection, old_pdf, new_pdf, options, staging):
    if os.name != "nt":
        os.setsid()
    connection.send({"type": "ready"})
    connection.recv()
    os._exit(7)


def _child_crashes_with_descendant(connection, old_pdf, new_pdf, options, staging):
    if os.name != "nt":
        os.setsid()
    connection.send({"type": "ready"})
    connection.recv()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    connection.send({"type": "progress", "pid": child.pid})
    os._exit(7)


def wait_for(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("Timed out waiting for the required state")


def process_alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes as w
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        api.CloseHandle.argtypes = [w.HANDLE]
        handle = api.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class ComparisonJobTests(unittest.TestCase):
    def test_cancel_reaps_descendant_and_removes_only_private_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            existing = root / "existing.html"
            existing.write_text("preserve me")
            cancel = threading.Event()
            descendant = []
            published = Mock()
            def progress(payload):
                descendant.append(payload["pid"])
                self.assertTrue(process_alive(payload["pid"]))
                cancel.set()
            started = time.monotonic()
            with self.assertRaises(ComparisonCancelled):
                run_isolated_comparison(root / "a.pdf", root / "b.pdf", DiffOptions(), root,
                                        cancel, progress, published, worker_target=_child_with_descendant)
            self.assertLess(time.monotonic() - started, 8)
            wait_for(lambda: not process_alive(descendant[0]), seconds=3)
            published.assert_not_called()
            self.assertEqual("preserve me", existing.read_text(encoding="utf-8"))
            self.assertEqual([existing], list(root.iterdir()))

    def test_worker_crash_is_error_and_cleans_temporary_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(RuntimeError, "退出|结束"):
                run_isolated_comparison(root / "a", root / "b", DiffOptions(), root,
                                        threading.Event(), lambda _: None, Mock(), worker_target=_child_crashes)
            self.assertEqual([], list(root.iterdir()))

    def test_crashed_leader_does_not_leave_ocr_descendant(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            descendants = []
            with self.assertRaisesRegex(RuntimeError, "退出|结束"):
                run_isolated_comparison(root / "a", root / "b", DiffOptions(), root,
                    threading.Event(), lambda p: descendants.append(p["pid"]), Mock(),
                    worker_target=_child_crashes_with_descendant)
            self.assertEqual(1, len(descendants))
            wait_for(lambda: not process_alive(descendants[0]), seconds=3)
            self.assertEqual([], list(root.iterdir()))

    def test_close_during_work_cancels_before_destroying_window(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = write_demo_pdfs(root / "inputs")
            api = ProtocolDiffWebApi()
            window = Mock()
            api.bind_window(window)
            observed = []
            window.destroy.side_effect = lambda: observed.append(api.get_run_state()["type"])
            api.run_comparison({"old_pdf": str(old), "new_pdf": str(new), "output_dir": str(root / "out")})
            self.assertFalse(api._handle_close_request())
            wait_for(lambda: bool(observed))
            self.assertEqual(["cancelled"], observed)
            self.assertTrue(api._handle_close_request())
            self.assertEqual([], list((root / "out").glob("*")))

    def test_real_desktop_async_cancel_then_success_and_unique_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = write_demo_pdfs(root / "inputs")
            api = ProtocolDiffWebApi()
            window = Mock()
            window.evaluate_js.side_effect = AssertionError("Supervisor must not wait for WebView")
            api.bind_window(window)
            config = {"old_pdf": str(old), "new_pdf": str(new), "output_dir": str(root / "out")}
            self.assertTrue(api.run_comparison(config)["ok"])
            self.assertTrue(api.cancel_comparison()["ok"])
            self.assertTrue(api.cancel_comparison()["ok"])
            wait_for(lambda: api.get_run_state()["type"] in ("cancelled", "error"))
            self.assertEqual("cancelled", api.get_run_state()["type"], api.get_run_state())
            paths = []
            for _ in range(2):
                self.assertTrue(api.run_comparison(config)["ok"])
                wait_for(lambda: api.get_run_state()["type"] in ("success", "error"), seconds=30)
                state = api.get_run_state()
                self.assertEqual("success", state["type"], state)
                html = Path(state["html_path"])
                self.assertIn("<html", html.read_text(encoding="utf-8").lower())
                data = json.loads((html.parent / "protocol_diff_data.json").read_text(encoding="utf-8"))
                self.assertTrue(data)  # Reopening here checks the actual published report.
                paths.append(html)
            self.assertNotEqual(paths[0], paths[1])
            self.assertTrue(paths[0].is_file())
            self.assertEqual([], list((root / "out").glob(".protocol-diff-*")))
            window.evaluate_js.assert_not_called()

    def test_cleanup_failure_keeps_ownership_until_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = write_demo_pdfs(root / "inputs")
            api = ProtocolDiffWebApi()
            window = Mock(); api.bind_window(window)
            release = threading.Event()
            stop_tree = comparison_job._stop_tree
            def flaky_stop(*args):
                if not release.is_set(): raise PermissionError("injected cleanup fault")
                return stop_tree(*args)
            config = {"old_pdf": str(old), "new_pdf": str(new), "output_dir": str(root / "out")}
            with patch.object(comparison_job, "_stop_tree", side_effect=flaky_stop):
                try:
                    api.run_comparison(config)
                    wait_for(lambda: api.get_run_state()["type"] == "cleanup_error", 30)
                    self.assertFalse(api.run_comparison(config)["ok"])
                    self.assertFalse(api._handle_close_request())
                    window.destroy.assert_not_called()
                    self.assertEqual(1, len(list((root / "out").glob(".protocol-diff-*"))))
                finally:
                    release.set()
                    wait_for(lambda: not api._running)
            self.assertEqual("cancelled", api.get_run_state()["type"])
            wait_for(lambda: window.destroy.called)
            self.assertEqual([], list((root / "out").iterdir()))

    def test_slow_old_auto_open_cannot_replace_new_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = write_demo_pdfs(root / "inputs")
            api = ProtocolDiffWebApi()
            arrived, release, returned = threading.Event(), threading.Event(), threading.Event()
            def slow_open(url):
                arrived.set()
                release.wait(20)
                returned.set()
                return False
            config = {"old_pdf": str(old), "new_pdf": str(new), "output_dir": str(root / "out")}
            with patch("protocol_pdf_diff.webview_gui.webbrowser.open", side_effect=slow_open):
                try:
                    api.run_comparison({**config, "auto_open": True})
                    self.assertTrue(arrived.wait(30))
                    first = api.get_run_state()["html_path"]
                    self.assertTrue(api.run_comparison(config)["ok"])
                    wait_for(lambda: api.get_run_state()["type"] == "success", 30)
                    latest = api.get_run_state()
                    self.assertNotEqual(first, latest["html_path"])
                finally: release.set()
                self.assertTrue(returned.wait(5))
                time.sleep(.05)
                self.assertEqual(latest, api.get_run_state())

    def test_cancel_wins_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = write_demo_pdfs(root / "inputs")
            api = ProtocolDiffWebApi()
            arrived, release = threading.Event(), threading.Event()
            publish = api._publish_report
            def blocked_publish(outputs, payload):
                arrived.set()
                if not release.wait(10):
                    raise RuntimeError("Test did not release publication")
                return publish(outputs, payload)
            api._publish_report = blocked_publish
            config = {"old_pdf": str(old), "new_pdf": str(new), "output_dir": str(root / "out")}
            api.run_comparison(config)
            try:
                self.assertTrue(arrived.wait(20))
                self.assertFalse(api.run_comparison(config)["ok"])
                self.assertTrue(api.cancel_comparison()["ok"])
            finally:
                release.set()
            wait_for(lambda: api.get_run_state()["type"] in ("cancelled", "error"))
            self.assertEqual("cancelled", api.get_run_state()["type"])
            self.assertEqual([], list((root / "out").iterdir()))


if __name__ == "__main__":
    unittest.main()
