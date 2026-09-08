"""Frozen reader-hostile mutation: discard every review action."""

from __future__ import annotations


def build_review_tasks(*_args, **_kwargs):
    return ()


def task_counts(_tasks):
    return {"detected": 0, "review": 0, "coverage": 0}
