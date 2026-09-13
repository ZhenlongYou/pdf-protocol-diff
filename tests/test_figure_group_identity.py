import unittest
from pathlib import Path
from dataclasses import replace
from test_source_char_integration import side, URI
from protocol_pdf_diff.models import *
from protocol_pdf_diff.visual_ownership import build_visual_owned_spans


def cross(captions="normal"):
    ops = []
    nps = []
    for identity, vals, pages, dest in [
        ("old", ["800 mV", "700 mV"], [1, 2], ops),
        ("new", ["900 mV", "800 mV"], [3, 4], nps),
    ]:
        for val, pn in zip(vals, pages):
            p, s = side(identity, val)
            dest.append(
                replace(
                    p,
                    page_number=pn,
                    blocks=tuple(replace(b, page_number=pn) for b in p.blocks),
                )
            )
    _, os = side("old", "800 mV")
    _, ns = side("new", "800 mV")
    os = replace(os, start_page=1, end_page=2)
    ns = replace(ns, start_page=3, end_page=4)
    c = SectionChange("modified", os, ns, 0.8, removed_snippets=["800 mV"])
    r = DiffResult(Path("old.pdf"), Path("new.pdf"), [os], [ns], [c], [])
    gs = []
    for a, b in [(1, 3), (2, 4)]:
        ov = ProseSourceVisual(a, (-1.0, -21.0, 201.0, 51.0), URI, 0, 0)
        nv = replace(ov, page_number=b)
        gs.append(
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
    if captions == "missing":
        gs = [replace(gs[0], new_figure_captions=()), gs[1]]
    if captions == "duplicate":
        gs = [
            replace(
                gs[0],
                new_figure_visuals=(gs[0].new_figure_visuals[0],) * 2,
                new_figure_captions=("Figure 1.",) * 2,
            ),
            gs[1],
        ]
    return build_visual_owned_spans(
        r,
        ExtractionResult(Path("old.pdf"), ops),
        ExtractionResult(Path("new.pdf"), nps),
        gs,
    )


class GroupTests(unittest.TestCase):
    def test_same_caption_other_group_cannot_supply_changed_value(self):
        self.assertFalse(cross().get("old:old", {}).get("800 mV"))

    def test_missing_counterpart_caption(self):
        self.assertFalse(cross("missing").get("old:old", {}).get("800 mV"))

    def test_duplicate_counterpart_caption(self):
        self.assertFalse(cross("duplicate").get("old:old", {}).get("800 mV"))


if __name__ == "__main__":
    unittest.main()
