"""Read-only progress events shared by the core pipeline and desktop UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from contextvars import ContextVar
from functools import wraps
import time


@dataclass(frozen=True)
class ProgressEvent:
    """One immutable observation of a real pipeline stage or page count."""

    stage: str
    side: str | None = None
    completed_pages: int | None = None
    total_pages: int | None = None
    unit: str = "页"
    detail: str = ""


ProgressObserver = Callable[[ProgressEvent], None]
_current = ContextVar("comparison_progress", default=None)


def progress_session(function):
    """Make the caller's observer available to deeply nested matching loops."""
    @wraps(function)
    def run(*args, **kwargs):
        token = _current.set((kwargs.get("progress_observer"), {}))
        try:
            return function(*args, **kwargs)
        finally:
            _current.reset(token)
    return run


def report_progress(stage, completed=None, total=None, *, unit="项", detail=""):
    """Report real counters, coalescing fast loops to ten updates per second."""
    context = _current.get()
    if context is None or context[0] is None:
        return
    observer, last = context
    now = time.monotonic()
    if last.get("stage") == stage and completed not in (0, total) and now - last.get("time", 0) < .1:
        return
    last.update(stage=stage, time=now)
    notify_progress(observer, ProgressEvent(stage=stage, completed_pages=completed,
                                          total_pages=total, unit=unit, detail=detail))


def notify_progress(
    observer: ProgressObserver | None,
    event: ProgressEvent,
) -> None:
    """Notify an observer without letting diagnostics change diff behavior."""

    if observer is None:
        return
    try:
        observer(event)
    except Exception:
        # Progress is diagnostic only. A UI/logging callback must never change
        # extraction, matching, visual evidence, or report content.
        return
