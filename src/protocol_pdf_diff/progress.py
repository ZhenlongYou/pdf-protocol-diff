"""Read-only progress events shared by the core pipeline and desktop UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressEvent:
    """One immutable observation of a real pipeline stage or page count."""

    stage: str
    side: str | None = None
    completed_pages: int | None = None
    total_pages: int | None = None


ProgressObserver = Callable[[ProgressEvent], None]


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
