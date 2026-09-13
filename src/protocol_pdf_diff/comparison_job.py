"""Own one isolated desktop comparison and its temporary output directory.

The supervisor never calls WebView. A ready/go handshake ensures the worker and
its OCR children are contained before any PDF work. Only the supervisor cleans
resources; the UI requests cancellation through a threading.Event.
"""
from __future__ import annotations

import ctypes
import multiprocessing
import os
import signal
import tempfile
import time
from pathlib import Path
from threading import Event
from typing import Callable


class ComparisonCancelled(Exception):
    """The user cancelled before the report publication boundary."""


class WindowsJob:
    """Parent-owned, non-inheritable Windows Job; all OCR children stay inside."""

    def __init__(self):
        from ctypes import wintypes as w

        class Limits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", w.DWORD), ("minimum", ctypes.c_size_t),
                        ("maximum", ctypes.c_size_t), ("active_limit", w.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", w.DWORD),
                        ("scheduling", w.DWORD)]

        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Limits), ("io", Counters),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in
                        ("user", "kernel", "period_user", "period_kernel")] + [
                        (name, w.DWORD) for name in ("faults", "total", "active", "terminated")]

        self._accounting = Accounting
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach(self, pid: int):
        handle = self.api.OpenProcess(0x0100 | 0x0001, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(handle)

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + 5
        while True:
            accounting = self._accounting()
            if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if accounting.active == 0:
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("比较进程仍在退出，请稍后重试。")
            time.sleep(0.02)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def _worker(connection, old_pdf, new_pdf, options, staging):
    """Spawn entry point: no PDF/OCR work before containment is acknowledged."""
    try:
        if os.name != "nt":
            os.setsid()
        connection.send({"type": "ready"})
        if connection.recv() != "go":
            return
        from .table_view_transaction import run_diff_transaction as run_diff, report_outcome
        from .comparison_session import comparison_scope
        from .progress import ProgressEvent
        from .reporting import write_reports
        from .webview_gui import ProtocolDiffWebApi

        def progress(event):
            connection.send({"type": "progress", "stage": event.stage,
                             "side": event.side, "completed_pages": event.completed_pages,
                             "total_pages": event.total_pages,
                             "unit": event.unit, "detail": event.detail})

        with comparison_scope():
            result = run_diff(old_pdf, new_pdf, options, progress_observer=progress)
            progress(ProgressEvent(stage="report"))
            outcome = report_outcome(result, staging, options, writer=write_reports)
            result, outputs = outcome.selected_result, outcome.outputs
            payload = ProtocolDiffWebApi._success_payload(result, outputs)
            connection.send({"type": "complete", "outputs": {k: str(v) for k, v in outputs.items()},
                             "payload": payload})
        # Keep the owner process alive until the supervisor has consumed the
        # terminal message. It owns the process group / Job until cleanup.
        connection.recv()
    except Exception as exc:
        try:
            connection.send({"type": "error", "message": str(exc) or type(exc).__name__})
        except (OSError, EOFError):
            pass
    finally:
        connection.close()


def _signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        # On Darwin an exited, unreaped leader can yield EPERM rather than
        # ESRCH. Reap and retry; never suppress a real permissions failure.
        process.join(0.05)
        if process.exitcode is None:
            raise
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return


def _stop_tree(process, group_ready: bool, job: WindowsJob | None):
    """Reap the worker and its inherited OCR descendants before UI recovery."""
    # Reap an already-exited leader first: Darwin reports EPERM for a
    # zombie-only process group until waitpid has collected its leader.
    # This does not remove the group if live OCR descendants remain.
    process.join(0)
    if job is not None:
        job.terminate()
    elif group_ready and os.name != "nt":
        _signal_group(process, signal.SIGTERM)
        process.join(0.3)
        _signal_group(process, signal.SIGKILL)
    elif process.is_alive():
        process.terminate()
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join(2)
    if process.is_alive():
        raise RuntimeError("无法结束比较进程。")
    if job is not None:
        job.close()
    process.close()


def run_isolated_comparison(
    old_pdf: Path, new_pdf: Path, options, output_dir: Path,
    cancel: Event, progress: Callable[[dict], None], publish: Callable[[dict, dict], object],
    *, worker_target=None,
):
    """Compute privately, clean the process tree, then offer complete output.

    ``publish`` owns the cancellation/publication arbitration under the
    controller lock. TemporaryDirectory removes only this unique task's output,
    even when starting the process, IPC, rendering or report writing fails.
    """
    if cancel.is_set():
        raise ComparisonCancelled()
    output_dir.mkdir(parents=True, exist_ok=True)
    context = multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix=".protocol-diff-", dir=output_dir) as staging:
        parent, child = context.Pipe()
        process = context.Process(target=worker_target or _worker,
                                  args=(child, old_pdf, new_pdf, options, staging),
                                  name="protocol-diff-comparison")
        started = False
        group_ready = False
        job = None
        terminal = None
        try:
            if cancel.is_set():
                raise ComparisonCancelled()
            process.start()
            started = True
            child.close()
            deadline = time.monotonic() + 30
            while terminal is None:
                if cancel.is_set():
                    raise ComparisonCancelled()
                if parent.poll(0.05):
                    try:
                        message = parent.recv()
                    except EOFError as exc:
                        raise RuntimeError("比较进程意外结束。") from exc
                    kind = message.get("type")
                    if kind == "ready":
                        if group_ready:
                            raise RuntimeError("比较进程重复启动。")
                        group_ready = True
                        if os.name == "nt":
                            job = WindowsJob()
                            job.attach(process.pid)
                        if cancel.is_set():
                            raise ComparisonCancelled()
                        parent.send("go")
                    elif kind == "progress":
                        progress(message)
                    elif kind == "error":
                        raise RuntimeError(message.get("message", "比较失败。"))
                    elif kind == "complete":
                        terminal = message
                    else:
                        raise RuntimeError("比较进程返回了无效状态。")
                elif not process.is_alive():
                    raise RuntimeError(f"比较进程意外退出（{process.exitcode}）。")
                if not group_ready and time.monotonic() >= deadline:
                    raise RuntimeError("比较进程启动超时。")
        finally:
            parent.close()
            child.close()
            if started:
                # Keep ownership and the private directory while cleanup is
                # incomplete. Never restore the form around a live OCR child.
                while True:
                    try:
                        _stop_tree(process, group_ready, job)
                        break
                    except Exception as exc:
                        progress({"type": "cleanup_error", "message":
                                  f"结束后台任务失败，正在重试清理：{exc}"})
                        time.sleep(0.5)
            else:
                process.close()
        if cancel.is_set():
            raise ComparisonCancelled()
        outputs = {k: Path(v) for k, v in terminal["outputs"].items()}
        source = outputs.get("report_dir")
        if source is None or source.parent.resolve() != Path(staging).resolve() or source.is_symlink():
            raise RuntimeError("报告临时目录无效。")
        for path in outputs.values():
            if not path.resolve().is_relative_to(source.resolve()) or path.is_symlink():
                raise RuntimeError("报告文件不在本次任务目录内。")
        return publish(outputs, terminal["payload"])
