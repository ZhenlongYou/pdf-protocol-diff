from pathlib import Path
import unittest
from dataclasses import replace
from protocol_pdf_diff.models import (
    DocumentBlock,
    DocumentBlockKind,
    PageText,
    Section,
    SectionChange,
    SnippetPair,
    DiffResult,
    ProseSourceVisual,
    ProseSourceVisualGroup,
    ExtractionResult,
)
from protocol_pdf_diff.visual_ownership import build_visual_owned_spans
from protocol_pdf_diff.reporting import _reader_section_change

URI = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aBZkAAAAASUVORK5CYII="


def page(pn, label, reverse=False):
    cap = DocumentBlock(
        pn,
        (0.0, -20.0, 100.0, -10.0),
        DocumentBlockKind.TEXT,
        "Figure 1.",
        0,
        "native",
        word_boxes=(
            ("Figure", 0.0, -20.0, 30.0, -10.0),
            ("1.", 35.0, -20.0, 45.0, -10.0),
        ),
    )
    lw = ((label, 10.0, 10.0, 30.0, 20.0), ("mV", 35.0, 10.0, 50.0, 20.0))
    bw = (("Stable", 110.0, 10.0, 135.0, 20.0), ("body.", 140.0, 10.0, 165.0, 20.0))
    words = bw + lw if reverse else lw + bw
    text = " ".join(w[0] for w in words)
    block = DocumentBlock(
        pn,
        (10.0, 10.0, 165.0, 20.0),
        DocumentBlockKind.TEXT,
        text,
        1,
        "native",
        word_boxes=words,
    )
    chars = []
    for word, x, y, x1, y1 in words:
        for i, ch in enumerate(word):
            chars.append(
                (
                    ch,
                    x + i * (x1 - x) / len(word),
                    y,
                    x + (i + 1) * (x1 - x) / len(word),
                    y1,
                    len(chars),
                )
            )
    return PageText(
        pn,
        text,
        blocks=(cap, block),
        vector_graphic_bboxes=((0.0, 0.0, 100.0, 40.0),),
        source_char_map=tuple(chars),
    )


def section(side, pages):
    return Section(
        side,
        "1 Models",
        "Models",
        1,
        ("1 Models",),
        ("1",),
        pages[0].page_number,
        pages[-1].page_number,
        "\n".join(p.text for p in pages),
    )


def scenario():
    old = [page(1, "800"), page(2, "700")]
    new = [page(3, "900"), page(4, "800", True)]
    a, b = section("old", old), section("new", new)
    c = SectionChange(
        "modified", a, b, 0.8, replaced_snippets=[SnippetPair(old[0].text, new[1].text)]
    )
    r = DiffResult(Path("old.pdf"), Path("new.pdf"), [a], [b], [c], [])
    groups = []
    for op, np in [(1, 3), (2, 4)]:
        ov = ProseSourceVisual(op, (-1.0, -21.0, 180.0, 41.0), URI, 0, 0)
        nv = ProseSourceVisual(np, (-1.0, -21.0, 180.0, 41.0), URI, 0, 0)
        groups.append(
            ProseSourceVisualGroup(
                "modified",
                "old",
                "new",
                old_figure_visuals=(ov,),
                new_figure_visuals=(nv,),
                old_figure_captions=("Figure 1.",),
                new_figure_captions=("Figure 1.",),
            )
        )
    spans = build_visual_owned_spans(
        r,
        ExtractionResult(Path("old.pdf"), old),
        ExtractionResult(Path("new.pdf"), new),
        groups,
    )
    reader = _reader_section_change(
        c, visual_owned_spans=(spans.get("old:old", {}), spans.get("new:new", {}))
    )
    output = {
        "old_snippet": old[0].text,
        "new_snippet": new[1].text,
        "actual_group_pairs": [[1, 3], [2, 4]],
        "group_values": [["800 mV", "900 mV"], ["700 mV", "800 mV"]],
        "spans": spans,
        "reader_missing": reader is None,
    }
    return output, groups


class WordGroupTests(unittest.TestCase):
    def test_cross_group_equal_text_cannot_authorize(self):
        output, _ = scenario()
        self.assertFalse(output["reader_missing"])
        self.assertFalse(output["spans"].get("old:old", {}))
        self.assertFalse(output["spans"].get("new:new", {}))

    def test_same_paired_figure_with_no_char_map_keeps_legacy_positive(self):
        _, groups = scenario()
        op = replace(page(1, "800"), source_char_map=())
        np = replace(page(3, "800", True), source_char_map=())
        a, b = section("old", [op]), section("new", [np])
        c = SectionChange(
            "modified", a, b, 0.8, replaced_snippets=[SnippetPair(op.text, np.text)]
        )
        r = DiffResult(Path("old.pdf"), Path("new.pdf"), [a], [b], [c], [])
        spans = build_visual_owned_spans(
            r,
            ExtractionResult(Path("old.pdf"), [op]),
            ExtractionResult(Path("new.pdf"), [np]),
            groups[:1],
        )
        self.assertIsNone(
            _reader_section_change(
                c,
                visual_owned_spans=(spans.get("old:old", {}), spans.get("new:new", {})),
            )
        )


if __name__ == "__main__":
    unittest.main()
