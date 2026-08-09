"""End-to-end gates for quantified gold-corpus change recognition."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from protocol_pdf_diff.accuracy_evaluation import (
    _actual_events,
    _event_matches,
    _match_expected_events,
    _read_markdown_evidence,
    _read_text_evidence,
    _read_visible_html_evidence,
    _read_visible_html_text,
    run_gold_accuracy_evaluation,
    validate_gold_accuracy_manifest,
)
from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import (
    DiffOptions,
    DiffResult,
    ExtractionResult,
    FormulaChange,
    FormulaVisual,
    PageText,
    Section,
    SectionChange,
    SnippetPair,
    TableVisual,
    VisualWatchdogAudit,
)
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.sample_data import write_multipage_text_pdf


def _read_evidence_fixture(value: str, *, suffix: str, reader):
    """Build one short report fixture without keeping temporary files around."""

    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / f"fixture{suffix}"
        path.write_text(value, encoding="utf-8")
        return reader(path)


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

    def test_gold_literal_does_not_match_a_larger_engineering_number(self) -> None:
        """Expected 10/20 mV must not score against actual 110/120 mV text."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_multipage_text_pdf(
                root / "old.pdf",
                [["1 Limits", "The old limit is 110 mV."]],
            )
            write_multipage_text_pdf(
                root / "new.pdf",
                [["1 Limits", "The new limit is 120 mV."]],
            )
            manifest = {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "numeric-boundary",
                        "required": True,
                        "old": {"path": "old.pdf"},
                        "new": {"path": "new.pdf"},
                        "oracle_complete": True,
                        "expected_events": [
                            {
                                "id": "wrong-short-value",
                                "kind": "text",
                                "old": "10 mV",
                                "new": "20 mV",
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
        self.assertEqual(0.0, summary["metrics"]["critical_recall"])

    def test_gold_literal_boundaries_and_case_are_technical_exact(self) -> None:
        actual = {
            "kind": "text",
            "old": "The limit is 10.023 UI for CMIT-LT.",
            "new": "The limit is 20.025 ui for cmit-lt.",
            "location": "",
        }

        self.assertFalse(
            _event_matches(
                {"kind": "text", "old": "0.023 UI", "new": "0.025 UI"},
                actual,
            )
        )
        self.assertFalse(
            _event_matches(
                {"kind": "text", "old": "CMIT-LT", "new": "CMIT-LT"},
                actual,
            )
        )
        self.assertFalse(
            _event_matches(
                {"kind": "text", "old": "0.023 UI", "new": "0.025 UI"},
                {
                    "kind": "text",
                    "old": "The limit is -0.023 UI.",
                    "new": "The limit is +0.025 UI.",
                    "location": "",
                },
            )
        )
        self.assertFalse(
            _event_matches(
                {"kind": "text", "old": "CMIT-LT", "new": "CMIS-LT"},
                {
                    "kind": "text",
                    "old": "Use CMIT-LT-ALT.",
                    "new": "Use CMIS-LT/DEBUG.",
                    "location": "",
                },
            )
        )

    def test_gold_actual_events_include_caption_only_table_card(self) -> None:
        """A technical table-title change counts even when every row is unchanged."""

        old_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1 mV receiver limits",
            bbox=(50.0, 100.0, 550.0, 300.0),
            image_data_uri="",
            row_texts=["Parameter | 10 mV"],
            grid_summary="grid",
        )
        new_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 2 MV receiver limits",
            bbox=(50.0, 100.0, 550.0, 300.0),
            image_data_uri="",
            row_texts=["Parameter | 10 mV"],
            grid_summary="grid",
        )
        old = ExtractionResult(
            pdf_path=Path("old_table.pdf"),
            pages=[PageText(page_number=1, text="1 Limits\nStable prose.")],
            table_visuals=[old_table],
        )
        new = ExtractionResult(
            pdf_path=Path("new_table.pdf"),
            pages=[PageText(page_number=1, text="1 Limits\nStable prose.")],
            table_visuals=[new_table],
        )
        result = compare_extractions(old, new, DiffOptions())
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            reader_blob = "\n".join(
                outputs[key].read_text(encoding="utf-8")
                for key in ("markdown", "html", "text")
            )

        table_events = [
            event for event in _actual_events(payload, reader_blob) if event["kind"] == "table"
        ]
        self.assertEqual(1, len(table_events))
        self.assertIn("Table 1 mV receiver limits", table_events[0]["old"])
        self.assertIn("Table 2 MV receiver limits", table_events[0]["new"])
        self.assertTrue(table_events[0]["reader_visible"])
        self.assertIn("old titles: Table 1 mV receiver limits", table_events[0]["location"])
        self.assertIn("new pages: 1", table_events[0]["location"])

    def test_gold_formula_actual_event_has_page_and_formula_location(self) -> None:
        """Repeated expressions can be disambiguated by audited source location."""

        payload = {
            "changes": [],
            "table_changes": [],
            "formula_changes": [
                {
                    "old_formula": {
                        "page_number": 8,
                        "formula_number": "(31-3)",
                        "semantic_text": "V_{old} = 10 mV",
                    },
                    "new_formula": {
                        "page_number": 10,
                        "formula_number": "(31-4)",
                        "semantic_text": "V_{new} = 12 mV",
                    },
                }
            ],
            "visual_review_items": [],
        }
        reader_blob = "V_{old} = 10 mV\nV_{new} = 12 mV"

        events = _actual_events(payload, reader_blob)

        self.assertEqual(1, len(events))
        self.assertEqual(
            "old page 8 formula (31-3); new page 10 formula (31-4)",
            events[0]["location"],
        )
        self.assertTrue(events[0]["reader_visible"])

    def test_gold_untitled_added_table_card_uses_a_real_reader_literal(self) -> None:
        """An untitled card is visible via the exact side descriptions reports render."""

        new_table = TableVisual(
            page_number=2,
            table_number=3,
            title="",
            bbox=(50.0, 100.0, 550.0, 300.0),
            image_data_uri="",
            row_texts=["Parameter | 10 mV"],
            grid_summary="grid",
        )
        old = ExtractionResult(
            pdf_path=Path("old_table.pdf"),
            pages=[PageText(page_number=1, text="1 Limits\nStable prose.")],
        )
        new = ExtractionResult(
            pdf_path=Path("new_table.pdf"),
            pages=[PageText(page_number=1, text="1 Limits\nStable prose.")],
            table_visuals=[new_table],
        )
        result = compare_extractions(old, new, DiffOptions())
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            reader_blob = "\n".join(
                outputs[key].read_text(encoding="utf-8")
                for key in ("markdown", "text")
            )

        cards = [
            event
            for event in _actual_events(payload, reader_blob)
            if event["kind"] == "table" and "无表题续段" in event.get("new", "")
        ]
        self.assertEqual(1, len(cards))
        self.assertEqual("无对应表格", cards[0]["old"])
        self.assertEqual("无表题续段（页 2）", cards[0]["new"])
        self.assertTrue(cards[0]["reader_visible"])

    def test_gold_reader_contract_requires_every_surface_or_no_surface(self) -> None:
        """An HTML-only leak or loss cannot be hidden by Markdown/TXT unioning."""

        payload = {
            "changes": [
                {
                    "role": "technical",
                    "report_location": "1 Limits",
                    "replaced_snippets": [{"old": "10 mV", "new": "12 mV"}],
                    "removed_snippets": [],
                    "added_snippets": [],
                }
            ],
            "table_changes": [],
            "formula_changes": [],
            "visual_review_items": [],
        }
        actual = _actual_events(
            payload,
            {
                "html": "The limit changed from 10 mV to 12 mV.",
                "markdown": "The limit changed from 10 mV to 12 mV.",
                "text": "The material values are missing here.",
            },
        )
        base_expected = {
            "kind": "text",
            "old": "10 mV",
            "new": "12 mV",
            "critical": True,
            "occurrences": 1,
        }

        for expected_visible in (True, False):
            _matched_expected, _matched_actual, failures = _match_expected_events(
                [{**base_expected, "reader_visible": expected_visible}],
                actual,
            )
            self.assertIn("reader visibility mismatch", failures[0])

    def test_gold_reader_visibility_is_scoped_to_the_specific_change_card(self) -> None:
        """One duplicate literal at A cannot certify a missing card at B."""

        payload = {
            "changes": [
                {
                    "role": "technical",
                    "report_location": location,
                    "replaced_snippets": [{"old": "10 mV", "new": "20 mV"}],
                    "removed_snippets": [],
                    "added_snippets": [],
                }
                for location in ("A Limits", "B Limits")
            ],
            "table_changes": [],
            "formula_changes": [],
            "visual_review_items": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            markdown = root / "report.md"
            text_report = root / "report.txt"
            html = root / "report.html"
            markdown.write_text(
                "### 1. 修改: A Limits\n旧 10 mV，新 20 mV。\n",
                encoding="utf-8",
            )
            text_report.write_text(
                "1. 修改: A Limits\n旧 10 mV，新 20 mV。\n",
                encoding="utf-8",
            )
            html.write_text(
                '<section id="change-1"><h3>A Limits</h3>'
                "<p>旧 10 mV，新 20 mV。</p></section>",
                encoding="utf-8",
            )
            events = _actual_events(
                payload,
                {
                    "markdown": _read_markdown_evidence(markdown),
                    "text": _read_text_evidence(text_report),
                    "html": _read_visible_html_evidence(html),
                },
            )

        self.assertTrue(events[0]["reader_visible"])
        self.assertFalse(events[1]["reader_visible"])
        self.assertEqual(
            {"markdown": False, "text": False, "html": False},
            events[1]["reader_visibility"],
        )

    def test_gold_visual_visibility_is_scoped_to_each_v_card(self) -> None:
        """A rendered V1 card cannot certify that JSON V2 was rendered."""

        payload = {
            "changes": [],
            "table_changes": [],
            "formula_changes": [],
            "visual_review_items": [
                {"old_page_number": 1, "new_page_number": 1},
                {"old_page_number": 2, "new_page_number": 2},
            ],
        }
        surfaces = {
            name: value
            for name, value in {
                "html": _read_evidence_fixture(
                    '<article id="visual-review-1"><h3>V1. old/new</h3></article>',
                    suffix=".html",
                    reader=_read_visible_html_evidence,
                ),
                "markdown": _read_evidence_fixture(
                    "### V1. old/new\n",
                    suffix=".md",
                    reader=_read_markdown_evidence,
                ),
                "text": _read_evidence_fixture(
                    "V1. old/new\n",
                    suffix=".txt",
                    reader=_read_text_evidence,
                ),
            }.items()
        }

        events = _actual_events(payload, surfaces)

        self.assertTrue(events[0]["reader_visible"])
        self.assertFalse(events[1]["reader_visible"])

    def test_gold_reader_visibility_counts_duplicate_events_within_one_card(self) -> None:
        """Two audited occurrences cannot both pass when the card renders one."""

        payload = {
            "changes": [
                {
                    "role": "technical",
                    "report_location": "A Limits",
                    "display_report_location": "A Limits",
                    "reader_card_id": "C1",
                    "replaced_snippets": [
                        {"old": "10 mV", "new": "20 mV"},
                        {"old": "10 mV", "new": "20 mV"},
                    ],
                    "removed_snippets": [],
                    "added_snippets": [],
                }
            ],
            "table_changes": [],
            "formula_changes": [],
            "visual_review_items": [],
        }
        surfaces = {
            "html": _read_evidence_fixture(
                '<section id="change-1"><h3>A Limits</h3><p>10 mV 20 mV</p></section>',
                suffix=".html",
                reader=_read_visible_html_evidence,
            ),
            "markdown": _read_evidence_fixture(
                "### 1. A Limits\n10 mV 20 mV\n",
                suffix=".md",
                reader=_read_markdown_evidence,
            ),
            "text": _read_evidence_fixture(
                "1. A Limits\n10 mV 20 mV\n",
                suffix=".txt",
                reader=_read_text_evidence,
            ),
        }

        events = _actual_events(payload, surfaces)

        self.assertTrue(events[0]["reader_visible"])
        self.assertFalse(events[1]["reader_visible"])

    def test_gold_html_formula_subscript_matches_bounded_notation(self) -> None:
        """HTML sub/sup DOM must normalize to the JSON/Markdown formula notation."""

        payload = {
            "changes": [],
            "table_changes": [],
            "formula_changes": [
                {
                    "old_formula": {
                        "page_number": 8,
                        "formula_number": "(31-3)",
                        "semantic_text": "V_{old} = 10 mV",
                    },
                    "new_formula": {
                        "page_number": 8,
                        "formula_number": "(31-3)",
                        "semantic_text": "V_{new} = 12 mV",
                    },
                }
            ],
            "visual_review_items": [],
        }
        surfaces = {
            "html": _read_evidence_fixture(
                '<section id="formula-1"><p>F1 (31-3) V<sub>old</sub> = 10 mV; '
                "V<sub>new</sub> = 12 mV</p></section>",
                suffix=".html",
                reader=_read_visible_html_evidence,
            ),
            "markdown": _read_evidence_fixture(
                "### F1. formula\n(31-3) `V_{old} = 10 mV` `V_{new} = 12 mV`\n",
                suffix=".md",
                reader=_read_markdown_evidence,
            ),
            "text": _read_evidence_fixture(
                "F1. formula\n(31-3) V_{old} = 10 mV V_{new} = 12 mV\n",
                suffix=".txt",
                reader=_read_text_evidence,
            ),
        }

        event = _actual_events(payload, surfaces)[0]

        self.assertEqual(
            {"html": True, "markdown": True, "text": True},
            event["reader_visibility"],
        )
        self.assertTrue(event["reader_visible"])

    def test_gold_unplaced_formula_uses_its_own_html_card_scope(self) -> None:
        """An unplaced F card remains visible beside an unrelated text card."""

        old_section = Section(
            section_id="old-a",
            heading="1 Limits",
            title="Limits",
            level=1,
            heading_path=("1 Limits",),
            number_path=("1",),
            start_page=1,
            end_page=1,
            body="The prose limit is 10 mV.",
        )
        new_section = Section(
            **{
                **old_section.__dict__,
                "section_id": "new-a",
                "body": "The prose limit is 12 mV.",
            }
        )
        old_formula = FormulaVisual(
            page_number=99,
            formula_number="(A-1)",
            bbox=(10.0, 20.0, 200.0, 40.0),
            image_data_uri="",
            source_text="V old = 10 mV",
            semantic_text="V_{old} = 10 mV",
            script_count=1,
        )
        new_formula = FormulaVisual(
            **{
                **old_formula.__dict__,
                "source_text": "V new = 12 mV",
                "semantic_text": "V_{new} = 12 mV",
            }
        )
        result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[old_section],
            new_sections=[new_section],
            changes=[
                SectionChange(
                    change_type="modified",
                    old_section=old_section,
                    new_section=new_section,
                    similarity=0.9,
                    replaced_snippets=[
                        SnippetPair(
                            "The prose limit is 10 mV.",
                            "The prose limit is 12 mV.",
                        )
                    ],
                )
            ],
            warnings=[],
            formula_changes=[
                FormulaChange(
                    change_type="modified",
                    old_formula=old_formula,
                    new_formula=new_formula,
                    similarity=0.8,
                    visual_similarity=0.8,
                    reason="Formula semantics changed.",
                )
            ],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            surfaces = {
                "html": _read_visible_html_evidence(outputs["html"]),
                "markdown": _read_markdown_evidence(outputs["markdown"]),
                "text": _read_text_evidence(outputs["text"]),
            }

        formula_event = next(
            event for event in _actual_events(payload, surfaces) if event["kind"] == "formula"
        )
        self.assertEqual("F1", payload["formula_changes"][0]["reader_card_id"])
        self.assertEqual(
            {"html": True, "markdown": True, "text": True},
            formula_event["reader_visibility"],
        )

    def test_gold_case_fails_when_visual_watchdog_coverage_is_incomplete(self) -> None:
        """Recognition metrics cannot pass when the default pixel audit failed."""

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
                        "id": "watchdog-incomplete",
                        "required": True,
                        "old": {"path": "old.pdf"},
                        "new": {"path": "new.pdf"},
                        "oracle_complete": False,
                        "expected_events": [
                            {
                                "id": "limit",
                                "kind": "text",
                                "old": "10 mV",
                                "new": "12 mV",
                                "critical": True,
                                "reader_visible": True,
                            }
                        ],
                    }
                ],
            }
            manifest_path = root / "gold.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            incomplete = VisualWatchdogAudit(
                enabled=True,
                attempted=True,
                backend_available=True,
                eligible_page_pair_count=1,
                checked_page_pair_count=0,
                failed_page_pair_count=1,
                ambiguous_page_count=0,
                excluded_region_count=0,
                complete=False,
                source_hashes_match=True,
            )
            with patch(
                "protocol_pdf_diff.compare.detect_visual_review_items",
                return_value=([], ["watchdog failed"], incomplete),
            ):
                summary = run_gold_accuracy_evaluation(manifest_path, corpus_root=root)

        self.assertEqual("fail", summary["status"])
        self.assertTrue(
            any(
                "visual watchdog coverage incomplete" in failure
                for failure in summary["cases"][0]["failures"]
            )
        )

    def test_text_only_gold_case_can_explicitly_disclaim_visual_coverage(self) -> None:
        """A deliberate semantic-only benchmark may waive, but must expose, pixel coverage."""

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
                        "id": "semantic-only-waiver",
                        "required": True,
                        "old": {"path": "old.pdf"},
                        "new": {"path": "new.pdf"},
                        "oracle_complete": False,
                        "visual_coverage_required": False,
                        "expected_events": [
                            {
                                "id": "limit",
                                "kind": "text",
                                "old": "10 mV",
                                "new": "12 mV",
                                "critical": True,
                                "reader_visible": True,
                            }
                        ],
                    }
                ],
            }
            manifest_path = root / "gold.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            incomplete = VisualWatchdogAudit(
                enabled=True,
                attempted=True,
                backend_available=True,
                eligible_page_pair_count=1,
                checked_page_pair_count=0,
                failed_page_pair_count=1,
                ambiguous_page_count=0,
                excluded_region_count=0,
                complete=False,
                source_hashes_match=True,
            )
            with patch(
                "protocol_pdf_diff.compare.detect_visual_review_items",
                return_value=([], ["layout incomparable"], incomplete),
            ):
                summary = run_gold_accuracy_evaluation(manifest_path, corpus_root=root)

        case = summary["cases"][0]
        self.assertEqual("pass", summary["status"])
        self.assertFalse(case["visual_coverage_required"])
        self.assertFalse(case["visual_coverage_complete"])

    def test_visual_gold_event_cannot_waive_visual_coverage(self) -> None:
        manifest = {
            "schema_version": 1,
            "cases": [
                {
                    "id": "invalid-visual-waiver",
                    "required": True,
                    "old": {"path": "old.pdf"},
                    "new": {"path": "new.pdf"},
                    "oracle_complete": False,
                    "visual_coverage_required": False,
                    "expected_events": [
                        {
                            "id": "visual",
                            "kind": "visual",
                            "old_page": 1,
                            "new_page": 1,
                            "critical": True,
                            "reader_visible": True,
                        }
                    ],
                }
            ],
        }

        failures = validate_gold_accuracy_manifest(manifest)

        self.assertTrue(any("visual events are expected" in failure for failure in failures))

    def test_gold_html_reader_stream_discards_images_and_collapsed_audit_body(self) -> None:
        """Visible HTML extraction must not retain base64 or closed-detail audit text."""

        with tempfile.TemporaryDirectory() as temp_dir:
            html_path = Path(temp_dir) / "report.html"
            html_path.write_text(
                "<html><body><p>Visible 53.125 GHz.</p>"
                "<img src=\"data:image/png;base64," + "A" * 200_000 + "\">"
                "<details><summary>Audit</summary>Hidden 10 mV.</details>"
                "</body></html>",
                encoding="utf-8",
            )

            visible = _read_visible_html_text(html_path)

        self.assertIn("Visible 53.125 GHz.", visible)
        self.assertIn("Audit", visible)
        self.assertNotIn("Hidden 10 mV.", visible)
        self.assertNotIn("AAAA", visible)


if __name__ == "__main__":
    unittest.main()
