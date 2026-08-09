"""End-to-end gates for quantified gold-corpus change recognition."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from protocol_pdf_diff.accuracy_evaluation import (
    run_gold_accuracy_evaluation,
    validate_gold_accuracy_manifest,
)
from protocol_pdf_diff.sample_data import write_multipage_text_pdf


class GoldAccuracyEvaluationTests(unittest.TestCase):
    """Metrics must be derived from the normal PDF-to-report path."""

    def test_manifest_rejects_float_schema_version(self) -> None:
        manifest = {
            "schema_version": 1.0,
            "cases": [
                {
                    "id": "schema-check",
                    "required": False,
                    "old": {"path": "old.pdf"},
                    "new": {"path": "new.pdf"},
                    "oracle_complete": False,
                    "expected_events": [
                        {
                            "id": "change",
                            "kind": "text",
                            "old": "old",
                            "new": "new",
                            "critical": False,
                            "reader_visible": True,
                        }
                    ],
                }
            ],
        }

        failures = validate_gold_accuracy_manifest(manifest)

        self.assertIn("schema_version must be exactly 1", failures)

    def test_complete_gold_oracle_measures_text_and_visual_recall_and_precision(self) -> None:
        """One critical value edit plus one graphic edit should score two exact hits."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "old.pdf",
                [
                    ["1 Limits", "The calibrated limit shall be 10 mV."],
                    ["2 Diagram", "The diagram below defines the signal path."],
                ],
                decorative_marks={2: "old"},
            )
            write_multipage_text_pdf(
                root / "new.pdf",
                [
                    ["1 Limits", "The calibrated limit shall be 12 mV."],
                    ["2 Diagram", "The diagram below defines the signal path."],
                ],
                decorative_marks={2: "new"},
            )
            manifest = {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "controlled-value-and-visual",
                        "required": True,
                        "old": {"path": "old.pdf"},
                        "new": {"path": "new.pdf"},
                        "oracle_complete": True,
                        "expected_events": [
                            {
                                "id": "critical-limit",
                                "kind": "text",
                                "old": "10 mV",
                                "new": "12 mV",
                                "critical": True,
                                "reader_visible": True,
                            },
                            {
                                "id": "diagram-pixels",
                                "kind": "visual",
                                "old_page": 2,
                                "new_page": 2,
                                "critical": False,
                                "reader_visible": True,
                            },
                        ],
                    }
                ],
            }
            manifest_path = root / "gold.json"
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False),
                encoding="utf-8",
            )

            summary = run_gold_accuracy_evaluation(manifest_path, corpus_root=root)

        self.assertEqual("pass", summary["status"])
        self.assertEqual(1.0, summary["metrics"]["recall"])
        self.assertEqual(1.0, summary["metrics"]["critical_recall"])
        self.assertEqual(1.0, summary["metrics"]["visual_recall"])
        self.assertEqual(1.0, summary["metrics"]["precision"])
        self.assertEqual(0, summary["metrics"]["false_negative_count"])

    def test_missing_critical_gold_event_fails_instead_of_reporting_a_false_green(self) -> None:
        """A wrong expected new unit/value must produce an explicit false negative."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "old.pdf",
                [["1 Limits", "The calibrated limit shall be 10 mV."]],
            )
            write_multipage_text_pdf(
                root / "new.pdf",
                [["1 Limits", "The calibrated limit shall be 12 mV."]],
            )
            manifest = {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "wrong-critical-oracle",
                        "required": True,
                        "old": {"path": "old.pdf"},
                        "new": {"path": "new.pdf"},
                        "oracle_complete": True,
                        "expected_events": [
                            {
                                "id": "wrong-limit",
                                "kind": "text",
                                "old": "10 mV",
                                "new": "13 mV",
                                "critical": True,
                                "reader_visible": True,
                            }
                        ],
                    }
                ],
            }
            manifest_path = root / "gold.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            summary = run_gold_accuracy_evaluation(manifest_path, corpus_root=root)

        self.assertEqual("fail", summary["status"])
        self.assertEqual(0.0, summary["metrics"]["recall"])
        self.assertEqual(0.0, summary["metrics"]["critical_recall"])
        self.assertEqual(1, summary["metrics"]["false_negative_count"])


if __name__ == "__main__":
    unittest.main()
