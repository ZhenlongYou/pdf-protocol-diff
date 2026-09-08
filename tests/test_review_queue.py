"""Regression checks for the report's cross-channel review queue."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from protocol_pdf_diff.models import (
    DiffOptions,
    DiffResult,
    Section,
    SectionChange,
    SnippetPair,
    TableChange,
    TableRowChange,
    VisualCoverageIssue,
    VisualReviewItem,
)
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.review_queue import build_review_tasks, task_counts


def _section(section_id: str, page: int, text: str) -> Section:
    return Section(
        section_id,
        "1 Limits",
        "Limits",
        1,
        ("1 Limits",),
        ("1",),
        page,
        page,
        text,
    )


class ReviewQueueTests(unittest.TestCase):
    """Actions stay separated from the underlying fact and pixel units."""

    def test_queue_does_not_add_cross_channel_counts_as_one_change_total(self) -> None:
        old = _section("old", 2, "The limit shall be 1.0 V.")
        new = _section("new", 3, "The limit shall be 1.5 V.")
        prose = SectionChange(
            "modified", old, new, 0.9,
            replaced_snippets=[SnippetPair(old.body, new.body)],
        )
        table = TableChange(
            "modified", (), (), 1.0, False,
            (TableRowChange("Supply", "1.0 V", "1.5 V", "实质变化"),),
        )
        visual = VisualReviewItem(2, 3, "modified", 0.9, 0.01, "文字层未解释")
        coverage = (
            VisualCoverageIssue(4, 5, "文字块内容不同，未进行像素比较。", "layout"),
            VisualCoverageIssue(6, 7, "文字块内容不同，未进行像素比较。", "layout"),
        )

        tasks = build_review_tasks((prose,), (table,), (visual,), coverage)

        self.assertEqual(["C1", "T1", "V1", "U1"], [task.task_id for task in tasks])
        self.assertEqual({"detected": 2, "review": 1, "coverage": 1}, task_counts(tasks))
        self.assertEqual(2, tasks[-1].fact_count)
        self.assertEqual("#visual-coverage", tasks[-1].href)
        self.assertNotIn("技术变化", tasks[-1].title)

    def test_mixed_source_card_keeps_confirmed_and_unresolved_facts_separate(self) -> None:
        old = _section("old", 2, "The limit shall be 1.0 V.")
        new = _section("new", 3, "The limit shall be 1.5 V.")
        prose = SectionChange(
            "modified", old, new, 0.9,
            replaced_snippets=[SnippetPair(old.body, new.body)],
            review_replaced_snippets=[SnippetPair("custom glyph A", "custom glyph B")],
            review_reason="glyph mapping is not proven",
        )

        tasks = build_review_tasks((prose,), (), (), ())

        self.assertEqual(["C1", "C1R"], [task.task_id for task in tasks])
        self.assertEqual({"detected": 1, "review": 1, "coverage": 0}, task_counts(tasks))
        self.assertEqual("#change-1", tasks[0].href)
        self.assertEqual("#change-1", tasks[1].href)

    def test_report_serializes_queue_and_keeps_each_evidence_card_reachable(self) -> None:
        old = _section("old", 2, "The limit shall be 1.0 V.")
        new = _section("new", 3, "The limit shall be 1.5 V.")
        change = SectionChange(
            "modified", old, new, 0.9,
            replaced_snippets=[SnippetPair(old.body, new.body)],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(
                DiffResult(Path("old.pdf"), Path("new.pdf"), [old], [new], [change], []),
                temp_dir,
                DiffOptions(visual_watchdog=False),
            )
            report = outputs["html"].read_text(encoding="utf-8")
            data = __import__("json").loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertIn('<section class="review-queue" id="review-queue">', report)
        self.assertIn('data-review-filter="detected"', report)
        self.assertIn('data-review-task="C1"', report)
        self.assertIn('href="#change-1"', report)
        self.assertEqual(1, data["review_queue"]["counts"]["detected"])
        self.assertEqual("C1", data["review_queue"]["items"][0]["task_id"])
        self.assertIn("不能相加", data["review_queue"]["note"])

    def test_report_queue_and_cards_share_detected_first_numbering(self) -> None:
        """A raw review-first diff must not make C1 link to the review card."""

        old_review = _section("old-review", 2, "Ambiguous old glyph")
        new_review = _section("new-review", 3, "Ambiguous new glyph")
        review = SectionChange(
            "review", old_review, new_review, 0.8,
            review_replaced_snippets=[SnippetPair(old_review.body, new_review.body)],
        )
        old_change = _section("old-change", 4, "Limit is 1.0 V")
        new_change = _section("new-change", 5, "Limit is 1.5 V")
        changed = SectionChange(
            "modified", old_change, new_change, 0.9,
            replaced_snippets=[SnippetPair(old_change.body, new_change.body)],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(
                DiffResult(
                    Path("old.pdf"), Path("new.pdf"),
                    [old_review, old_change], [new_review, new_change],
                    [review, changed], [],
                ),
                temp_dir,
                DiffOptions(visual_watchdog=False),
            )
            report = outputs["html"].read_text(encoding="utf-8")
            data = __import__("json").loads(outputs["json"].read_text(encoding="utf-8"))

        tasks = data["review_queue"]["items"]
        self.assertEqual(("C1", "#change-1"), (tasks[0]["task_id"], tasks[0]["href"]))
        first_start = report.index('id="change-1"')
        self.assertIn("1.5", report[first_start:])
        self.assertIn('revealHashTarget(sourceLink.hash)', report)
        self.assertIn("node.open = true", report)
