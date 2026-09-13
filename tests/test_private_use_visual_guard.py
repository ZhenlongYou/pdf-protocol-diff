"""Unknown glyph identity must remain visible despite matching source positions."""
from dataclasses import replace
from pathlib import Path
import unittest

from test_bilateral_figure_guard import run as direct_run
from test_word_figure_group import page, section, URI
from protocol_pdf_diff.models import (
    DiffResult, ExtractionResult, ProseSourceVisual, ProseSourceVisualGroup,
    SectionChange, SnippetPair,
)
from protocol_pdf_diff.reporting import _reader_section_change
from protocol_pdf_diff.source_char_evidence import equivalent_char_ownership
from protocol_pdf_diff.visual_ownership import apply_owned_spans, build_visual_owned_spans


def word_only(label):
    old = replace(page(1, label), source_char_map=())
    new = replace(page(3, label, True), source_char_map=())
    a, b = section("old", [old]), section("new", [new])
    change = SectionChange("modified", a, b, .8,
                           replaced_snippets=[SnippetPair(old.text, new.text)])
    result = DiffResult(Path("old.pdf"), Path("new.pdf"), [a], [b], [change], [])
    group = ProseSourceVisualGroup("modified", "old", "new",
        old_figure_visuals=(ProseSourceVisual(1, (-1., -21., 180., 41.), URI, 0, 0),),
        new_figure_visuals=(ProseSourceVisual(3, (-1., -21., 180., 41.), URI, 0, 0),),
        old_figure_captions=("Figure 1.",), new_figure_captions=("Figure 1.",))
    spans = build_visual_owned_spans(result, ExtractionResult(Path("old.pdf"), [old]),
                                   ExtractionResult(Path("new.pdf"), [new]), [group])
    reader = _reader_section_change(change,
        visual_owned_spans=(spans.get("old:old", {}), spans.get("new:new", {})))
    return spans, reader, change


class PrivateUseVisualGuardTests(unittest.TestCase):
    def test_equal_pua_coordinates_are_not_font_identity(self):
        for char in ("\uf061", "\U000f0001", "\U00100001"):
            proof = dict(visual_group=(0, "old", "new"), caption="Figure 1",
                         box=(0., 0., 10., 10.), chars=[(char, 1., 1., 2., 2., 0)])
            other = {**proof, "font_sha256": "different-font", "glyph_id": 999}
            self.assertFalse(equivalent_char_ownership(proof, other))

    def test_direct_native_char_path_keeps_unknown_glyph(self):
        for char in ("\uf061", "\U000f0001", "\U00100001"):
            spans, reader = direct_run(char, char)
            self.assertFalse(spans.get("old:old", {}).get(char))
            self.assertIsNotNone(reader)
            self.assertEqual([char], reader.removed_snippets)

    def test_word_only_path_cannot_bypass_rejected_char_proof(self):
        for char in ("\uf061", "\U000f0001", "\U00100001"):
            spans, reader, original = word_only(char)
            self.assertFalse(spans.get("old:old", {}))
            self.assertFalse(spans.get("new:new", {}))
            self.assertIsNotNone(reader)
            self.assertEqual(original.replaced_snippets, reader.replaced_snippets)

    def test_shared_consumer_rejects_unproven_external_intervals(self):
        for char in ("\uf061", "\U000f0001", "\U00100001"):
            value = char + " Stable body."
            self.assertEqual(value, apply_owned_spans(value, [(0, 1)]))

    def test_ascii_native_character_and_word_proofs_remain_usable(self):
        spans, reader = direct_run("800 mV", "800 mV")
        self.assertTrue(spans["old:old"]["800 mV"])
        self.assertIsNone(reader)
        spans, reader, _ = word_only("800")
        self.assertTrue(spans["old:old"])
        self.assertIsNone(reader)

    def test_unrelated_private_use_tail_is_never_removed(self):
        value = "800 mV \uf061"
        self.assertEqual("\uf061", apply_owned_spans(value, [(0, 6)]))


if __name__ == "__main__":
    unittest.main()
