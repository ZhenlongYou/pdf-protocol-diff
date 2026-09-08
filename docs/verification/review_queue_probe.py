"""Exercise the typed review queue with frozen evidence-channel inputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from protocol_pdf_diff.models import (
    Section,
    SectionChange,
    SnippetPair,
    TableChange,
    TableRowChange,
    VisualCoverageIssue,
    VisualReviewItem,
)
from protocol_pdf_diff.review_queue import build_review_tasks, task_counts

TEST_ID = "REVIEW_QUEUE_TEST"


def _section(identifier: str, page: int) -> Section:
    return Section(identifier, "1 Limits", "Limits", 1, ("1 Limits",), ("1",), page, page, "The limit shall be 1.0 V.")


def _tasks(payload: dict[str, object]):
    old, new = _section("old", 1), _section("new", 2)
    prose = []
    for status in payload["prose"]:
        if status == "review":
            prose.append(SectionChange("review", old, new, 0.4, review_replaced_snippets=[SnippetPair(old.body, new.body)], review_reason="source ownership uncertain"))
        else:
            prose.append(SectionChange("modified", old, new, 0.9, replaced_snippets=[SnippetPair(old.body, new.body)]))
    tables = []
    for status in payload["table"]:
        row_kind = "需人工复核" if status == "review" else "实质变化"
        tables.append(TableChange("review" if status == "review" else "modified", (), (), 0.9, False, (TableRowChange("limit", "1.0 V", "1.5 V", row_kind),)))
    visual = (VisualReviewItem(1, 2, "modified", 0.9, 0.01, "unexplained pixels", focus_regions=((1, 2, 3, 4),)),) if payload["visual"] else ()
    coverage = tuple(VisualCoverageIssue(3 + index, 4 + index, reason, category) for index, (category, reason) in enumerate(payload["coverage"]))
    return build_review_tasks(prose, tables, visual, coverage)


def _check(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = _tasks(payload)
    expected = payload["expected"]
    if [task.task_id for task in tasks] != expected["ids"]:
        raise AssertionError("REVIEW_QUEUE_STATUS_OR_LINK_LOST")
    if task_counts(tasks) != expected["counts"]:
        raise AssertionError("REVIEW_QUEUE_STATUS_OR_LINK_LOST")
    facts = sum(task.fact_count for task in tasks if task.status == "detected")
    if facts != expected["detected_facts"]:
        raise AssertionError("REVIEW_QUEUE_FACTS_MIXED")
    if any(task.status == "coverage" and "技术变化" in task.title for task in tasks):
        raise AssertionError("REVIEW_QUEUE_COVERAGE_MISLABELLED")
    if any(not task.href.startswith("#") for task in tasks):
        raise AssertionError("REVIEW_QUEUE_SOURCE_LINK_LOST")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixtures", nargs="+")
    args = parser.parse_args()
    print(TEST_ID)
    try:
        for fixture in args.fixtures:
            _check(Path(fixture))
    except (AssertionError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(exc)
        return 1
    print("REVIEW_QUEUE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
