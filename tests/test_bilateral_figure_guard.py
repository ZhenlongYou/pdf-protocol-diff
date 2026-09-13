import unittest
from pathlib import Path
from dataclasses import replace
from test_source_char_integration import side, URI
from protocol_pdf_diff.models import *
from protocol_pdf_diff.visual_ownership import build_visual_owned_spans
from protocol_pdf_diff.reporting import _reader_section_change


def run(
    old,
    new,
    representation="direct",
    missing=False,
    duplicate=False,
    outside=False,
    missing_image=False,
    addition=False,
    extra_pair=None,
):
    op, os = side("old", old)
    np, ns = side("new", new)
    if missing:
        np = replace(np, source_char_map=())
    if duplicate:
        np = replace(np, text=np.text + " " + np.text)
    if outside:
        shift = lambda w: (w[0], w[1], w[2] + 100, w[3], w[4] + 100, *w[5:])
        np = replace(
            np,
            source_char_map=tuple(shift(c) for c in np.source_char_map),
            blocks=tuple(
                replace(b, word_boxes=tuple(shift(w) for w in b.word_boxes))
                if b.reading_order
                else b
                for b in np.blocks
            ),
        )
    if representation == "replacement":
        op = replace(op, vector_graphic_bboxes=((10.0, 0.0, 45.0, 50.0),))
        np = replace(np, vector_graphic_bboxes=((10.0, 0.0, 45.0, 50.0),))
        # The new-side body is physically outside the drawing, as in the reported case.
        shift = lambda w: (w[0], w[1] + 28, w[2], w[3] + 28, w[4], *w[5:])
        np = replace(
            np,
            source_char_map=tuple(shift(c) for c in np.source_char_map),
            blocks=tuple(
                replace(b, word_boxes=tuple(shift(w) for w in b.word_boxes))
                if b.reading_order
                else b
                for b in np.blocks
            ),
        )
    pairs = [SnippetPair(old, new)] if representation == "replacement" else []
    c = SectionChange(
        "removed" if representation == "section" else "modified",
        os,
        None if representation == "section" else ns,
        0.8,
        removed_snippets=[] if pairs else [old],
        replaced_snippets=pairs,
        audit_removed_snippets=[] if pairs else [old],
        audit_replaced_snippets=pairs,
    )
    if addition:
        c = replace(
            c,
            change_type="added" if representation == "section" else "modified",
            old_section=None if representation == "section" else os,
            new_section=ns,
            removed_snippets=[],
            audit_removed_snippets=[],
            added_snippets=[new],
            audit_added_snippets=[new],
        )
    if extra_pair is not None:
        c = replace(
            c, replaced_snippets=[extra_pair], audit_replaced_snippets=[extra_pair]
        )
    r = DiffResult(Path("old.pdf"), Path("new.pdf"), [os], [ns], [c], [])
    v = ProseSourceVisual(
        1, (-1.0, -21.0, 201.0, 51.0), "" if missing_image else URI, 0, 0
    )
    g = [
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
    spans = build_visual_owned_spans(
        r,
        ExtractionResult(Path("old.pdf"), [op]),
        ExtractionResult(Path("new.pdf"), [np]),
        g,
    )
    return spans, _reader_section_change(
        c, visual_owned_spans=(spans.get("old:old", {}), spans.get("new:new", {}))
    )


class BilateralTests(unittest.TestCase):
    def test_replacement_cannot_hide_numeric_prefix_from_same_remainder(self):
        old = "800 mV Text remains."
        new = "Text remains."
        spans, r = run(old, new, "replacement")
        self.assertIsNotNone(r)
        self.assertEqual([SnippetPair(old, new)], r.replaced_snippets)

    def test_direct_and_deleted_section_preserve_real_numeric_deletion(self):
        for form in ["direct", "section"]:
            spans, r = run("800 mV", "900 mV", form)
            self.assertIsNotNone(r)
            self.assertEqual(["800 mV"], r.removed_snippets)

    def test_direct_and_added_section_preserve_real_numeric_addition(self):
        for form in ["direct", "section"]:
            spans, r = run("900 mV", "800 mV", form, addition=True)
            self.assertIsNotNone(r)
            self.assertEqual(["800 mV"], r.added_snippets)

    def test_conflicting_replacement_denial_beats_recovered_direct(self):
        spans, r = run(
            "800 mV", "800 mV 900 mV", extra_pair=SnippetPair("800 mV", "900 mV")
        )
        self.assertFalse(spans.get("old:old", {}).get("800 mV"))
        self.assertIsNotNone(r)
        self.assertEqual(["800 mV"], r.removed_snippets)

    def test_same_native_value_in_other_figure_can_own_direct(self):
        spans, r = run("800 mV", "800 mV")
        self.assertTrue(spans["old:old"]["800 mV"])
        self.assertIsNone(r)

    def test_counterpart_must_have_unique_complete_native_inside_graphic_source(self):
        for options in [
            dict(missing=True),
            dict(duplicate=True),
            dict(outside=True),
            dict(missing_image=True),
        ]:
            spans, r = run("800 mV", "800 mV", **options)
            self.assertIsNotNone(r)
            self.assertEqual(["800 mV"], r.removed_snippets)


if __name__ == "__main__":
    unittest.main()
