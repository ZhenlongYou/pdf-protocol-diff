"""Generate a complete report and reopen its typed reader queue data."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from protocol_pdf_diff.models import DiffOptions, DiffResult, Section, SectionChange, SnippetPair, VisualReviewItem
from protocol_pdf_diff.reporting import write_reports


def _section(identifier: str, page: int, text: str) -> Section:
    return Section(identifier, "1 Limits", "Limits", 1, ("1 Limits",), ("1",), page, page, text)


def main() -> int:
    old = _section("old", 1, "The limit shall be +1.50 mV.")
    new = _section("new", 2, "The limit shall be -1.50 mV.")
    change = SectionChange("modified", old, new, 0.9, replaced_snippets=[SnippetPair(old.body, new.body)])
    result = DiffResult(
        Path("old.pdf"), Path("new.pdf"), [old], [new], [change], [],
        visual_review_items=[VisualReviewItem(1, 2, "modified", 0.9, 0.01, "unexplained pixels", focus_regions=((1, 2, 3, 4),))],
    )
    with tempfile.TemporaryDirectory(prefix="review-queue-real-") as temp_dir:
        outputs = write_reports(result, temp_dir, DiffOptions(visual_watchdog=False))
        html = outputs["html"].read_text(encoding="utf-8")
        data = json.loads(outputs["json"].read_text(encoding="utf-8"))
        queue = data["review_queue"]
        if queue["counts"] != {"detected": 1, "review": 1, "coverage": 0}:
            raise AssertionError("REAL_QUEUE_COUNTS")
        if [task["task_id"] for task in queue["items"]] != ["C1", "V1"]:
            raise AssertionError("REAL_QUEUE_IDENTIFIERS")
        for token in (
            'data-review-filter="detected"',
            'href="#change-1"',
            'href="#visual-review-1"',
            '展开 1 页视觉复核证据（默认收起，全部保留；V1. 至 V1.）',
            'revealHashTarget(sourceLink.hash)',
            'node.open = true',
        ):
            if token not in html:
                raise AssertionError("REAL_QUEUE_REPORT_LINK")
    artifact = ROOT / "docs" / "verification" / "out" / "review-queue-real-path.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({"queue_counts": queue["counts"], "source_links": 2}, separators=(",", ":")) + "\n", encoding="utf-8")
    print("REAL_REVIEW_QUEUE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
