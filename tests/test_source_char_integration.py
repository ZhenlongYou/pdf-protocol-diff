from pathlib import Path
from protocol_pdf_diff.models import (
    DiffResult,
    DocumentBlock,
    DocumentBlockKind,
    ExtractionResult,
    PageText,
    ProseSourceVisual,
    ProseSourceVisualGroup,
    Section,
    SectionChange,
    SnippetPair,
)
from protocol_pdf_diff.visual_ownership import build_visual_owned_spans
from protocol_pdf_diff.reporting import _reader_section_change


def side(identity, value):
    words = []
    chars = []
    x = 10.0
    idx = 0
    for word in value.split():
        words.append((word, x, 10.0, x + len(word) * 4, 18.0))
        for ch in word:
            chars.append((ch, x, 10.0, x + 4, 18.0, idx))
            x += 4
            idx += 1
        x += 4
    caption = DocumentBlock(
        1,
        (10.0, -20.0, 100.0, -10.0),
        DocumentBlockKind.TEXT,
        "Figure 1.",
        0,
        "native",
        word_boxes=(
            ("Figure", 10.0, -20.0, 34.0, -10.0),
            ("1.", 38.0, -20.0, 46.0, -10.0),
        ),
    )
    body = DocumentBlock(
        1,
        (10.0, 10.0, x, 18.0),
        DocumentBlockKind.TEXT,
        value,
        1,
        "native",
        word_boxes=tuple(words),
    )
    page = PageText(
        1,
        value,
        blocks=(caption, body),
        vector_graphic_bboxes=((0.0, 0.0, 200.0, 50.0),),
        source_char_map=tuple(chars),
    )
    section = Section(
        identity,
        "1 Reference Model",
        "Reference Model",
        1,
        ("1 Reference Model",),
        ("1",),
        1,
        1,
        value,
    )
    return page, section


from dataclasses import replace
from unittest.mock import patch
import unittest
from protocol_pdf_diff import source_char_evidence as evidence

URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aBZkAAAAASUVORK5CYII="


def run(old, new, pairs=None, missing=False, image=True, box=None):
    op, os = side("old", old)
    np, ns = side("new", new)
    if missing:
        np = replace(np, source_char_map=())
    if box is not None:
        op = replace(op, vector_graphic_bboxes=(box,))
        np = replace(np, vector_graphic_bboxes=(box,))
    c = SectionChange(
        "modified",
        os,
        ns,
        0.8,
        replaced_snippets=pairs or [SnippetPair(old, new)],
        audit_replaced_snippets=pairs or [SnippetPair(old, new)],
    )
    r = DiffResult(Path("old.pdf"), Path("new.pdf"), [os], [ns], [c], [])
    v = ProseSourceVisual(1, (-1.0, -21.0, 201.0, 51.0), URI if image else "", 0, 0)
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


class IntegrationTests(unittest.TestCase):
    def test_changed_values_not_hidden_by_legacy_with_or_without_maps(self):
        for missing in [False, True]:
            spans, r = run("Voltage 800 mV", "Voltage 900 mV", missing=missing)
            self.assertIsNotNone(r)
            self.assertEqual(
                [SnippetPair("Voltage 800 mV", "Voltage 900 mV")], r.replaced_snippets
            )

    def test_different_two_sided_labels_with_same_remaining_body_are_not_equal(self):
        old = "Voltage 800 mV The unchanged requirement."
        new = "Voltage 900 mV The unchanged requirement."
        for missing in [False, True]:
            spans, r = run(old, new, missing=missing, box=(0.0, 0.0, 69.0, 50.0))
            self.assertIsNotNone(r)
            self.assertEqual([SnippetPair(old, new)], r.replaced_snippets)

    def test_shared_value_any_denial_wins_in_both_pair_orders(self):
        pairs = [
            SnippetPair("Voltage 800 mV", "Voltage 800 mV"),
            SnippetPair("Voltage 800 mV", "Voltage 900 mV"),
        ]
        for ps in [pairs, list(reversed(pairs))]:
            spans, r = run("Voltage 800 mV", "Voltage 800 mV Voltage 900 mV", pairs=ps)
            self.assertFalse(spans.get("old:old", {}).get("Voltage 800 mV"))
            self.assertIsNotNone(r)
            self.assertIn(
                SnippetPair("Voltage 800 mV", "Voltage 900 mV"), r.replaced_snippets
            )

    def test_no_image_never_enters_new_char_proof(self):
        seen = []
        original = evidence.char_owned_candidate

        def observe(value, section, pages, boxes):
            seen.append(dict(boxes))
            return original(value, section, pages, boxes)

        with patch.object(evidence, "char_owned_candidate", observe):
            run("Alpha Beta", "Beta Alpha", image=False)
        self.assertTrue(seen)
        self.assertTrue(all(not x for x in seen))


if __name__ == "__main__":
    unittest.main()
