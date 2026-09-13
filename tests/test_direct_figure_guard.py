from pathlib import Path
from dataclasses import replace
import unittest
from test_source_char_integration import side, URI
from protocol_pdf_diff.models import *
from protocol_pdf_diff.visual_ownership import build_visual_owned_spans
from protocol_pdf_diff.reporting import _reader_section_change


def run(
    *,
    removed=(),
    added=(),
    audit_removed=None,
    audit_added=None,
    pairs=(),
    table=False,
    missing_map=False,
    new_text=None,
):
    old = "Limit 800 mV"
    new = new_text or "Limit 900 mV"
    op, os = side("old", old)
    np, ns = side("new", new)
    if missing_map:
        np = replace(np, source_char_map=())
    c = SectionChange(
        "modified",
        os,
        ns,
        0.8,
        removed_snippets=list(removed),
        added_snippets=list(added),
        replaced_snippets=list(pairs),
        audit_removed_snippets=audit_removed,
        audit_added_snippets=audit_added,
        audit_replaced_snippets=list(pairs),
    )
    r = DiffResult(Path("old.pdf"), Path("new.pdf"), [os], [ns], [c], [])
    v = ProseSourceVisual(1, (-1.0, -21.0, 201.0, 51.0), URI, 0, 0)
    groups = [
        ProseSourceVisualGroup(
            "modified",
            "old",
            "new",
            old_figure_visuals=(v,),
            new_figure_visuals=(v,),
            old_figure_captions=("Figure 1.",),
            new_figure_captions=("Figure 1.",),
        )
    ]
    if table:
        groups = []
        r.old_table_visuals[:] = [
            TableVisual(
                1,
                1,
                "Table 1",
                (0, 0, 200, 50),
                "",
                [old],
                "",
                content_fully_represented=True,
                row_alignment_reliable=True,
                data_rows_fully_represented=True,
            )
        ]
        r.new_table_visuals[:] = [
            TableVisual(
                1,
                1,
                "Table 1",
                (0, 0, 200, 50),
                "",
                [new],
                "",
                content_fully_represented=True,
                row_alignment_reliable=True,
                data_rows_fully_represented=True,
            )
        ]
    spans = build_visual_owned_spans(
        r,
        ExtractionResult(Path("old.pdf"), [op]),
        ExtractionResult(Path("new.pdf"), [np]),
        groups,
    )
    reader = _reader_section_change(
        c, visual_owned_spans=(spans.get("old:old", {}), spans.get("new:new", {}))
    )
    return spans, reader


class DirectGuardTests(unittest.TestCase):
    def test_two_direct_values_survive_with_or_without_char_map(self):
        for missing in [False, True]:
            spans, r = run(
                removed=["Limit 800 mV"], added=["Limit 900 mV"], missing_map=missing
            )
            self.assertIsNotNone(r)
            self.assertEqual(["Limit 800 mV"], r.removed_snippets)
            self.assertEqual(["Limit 900 mV"], r.added_snippets)

    def test_display_and_audit_union_prevents_truncated_display_bypass(self):
        spans, r = run(
            removed=["Limit 800 mV"],
            audit_removed=["Limit 800 mV"],
            audit_added=["Limit 900 mV"],
        )
        self.assertIsNotNone(r)
        self.assertEqual(["Limit 800 mV"], r.removed_snippets)
        self.assertEqual(["Limit 900 mV"], r.audit_added_snippets)

    def test_only_one_direct_side_preserves_real_change(self):
        for kwargs in [dict(removed=["Limit 800 mV"]), dict(added=["Limit 900 mV"])]:
            spans, r = run(**kwargs)
            self.assertIsNotNone(r)

    def test_table_intervals_are_not_revoked(self):
        spans, r = run(removed=["Limit 800 mV"], added=["Limit 900 mV"], table=True)
        self.assertTrue(spans["old:old"]["Limit 800 mV"])
        self.assertTrue(spans["new:new"]["Limit 900 mV"])

    def test_existing_counterpart_can_remove_false_deletion_but_keeps_new_value(self):
        spans, r = run(
            removed=["Limit 800 mV"],
            added=["Limit 900 mV"],
            pairs=[SnippetPair("Limit 800 mV", "Limit 800 mV")],
            new_text="Limit 800 mV Limit 900 mV",
        )
        self.assertTrue(spans.get("old:old", {}).get("Limit 800 mV"))
        self.assertFalse(spans.get("new:new", {}).get("Limit 900 mV"))
        self.assertIsNotNone(r)
        self.assertTrue(
            spans.get("new:new", {}).get("Limit 800 mV")
        )  # The competing replacement really did authorize its other side.
        self.assertEqual([], r.removed_snippets)
        self.assertEqual(["Limit 900 mV"], r.added_snippets)


if __name__ == "__main__":
    unittest.main()
