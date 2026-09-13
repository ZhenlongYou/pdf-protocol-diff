import unittest
from types import SimpleNamespace as NS
from dataclasses import replace
from protocol_pdf_diff.source_char_evidence import (
    source_char_map_for_final_text,
    char_owned_candidate,
    equivalent_char_ownership,
)
from protocol_pdf_diff.models import PageText, Section


def tm(text):
    tuples = []
    for i, c in enumerate(text):
        tuples.append(
            (
                c,
                None
                if c.isspace()
                else dict(x0=i * 3, top=10, x1=i * 3 + 2, bottom=18),
            )
        )
    return NS(
        tuples=tuples, as_string=text, line_dir_render="ttb", char_dir_render="ltr"
    )


def page(text):
    return PageText(
        1, text, source_char_map=source_char_map_for_final_text(text, tm(text))
    )


def sec(text):
    return Section("s", "1 A", "A", 1, ("1 A",), ("1",), 1, 1, text)


BOX = (0, 0, 200, 30)


def prove(p, value=None, box=BOX):
    return char_owned_candidate(
        value or p.text, sec(p.text), [p], {1: [(box, "Figure 1. Test", (0, "s", "s"))]}
    )


class CharBridgeTests(unittest.TestCase):
    def test_unchanged_order_only(self):
        self.assertTrue(source_char_map_for_final_text("A \nB", tm("A B")))
        self.assertFalse(source_char_map_for_final_text("B A", tm("A B")))
        self.assertFalse(source_char_map_for_final_text("A", tm("A B")))

    def test_missing_geometry_and_direction(self):
        t = tm("A")
        t.tuples = [("A", None)]
        self.assertFalse(source_char_map_for_final_text("A", t))
        t = tm("A")
        t.char_dir_render = "rtl"
        self.assertFalse(source_char_map_for_final_text("A", t))

    def test_duplicate_snippet_denies(self):
        self.assertIsNone(prove(page("Axis Axis"), "Axis"))

    def test_overlapping_occurrences_and_token_boundaries(self):
        self.assertIsNone(prove(page("a a a"), "a a"))
        self.assertIsNone(prove(page("aaaa"), "aaa"))
        self.assertIsNone(prove(page("GainDb"), "Gain"))

    def test_stale_map_denies(self):
        self.assertIsNone(prove(replace(page("Axis"), text="Bxis")))

    def test_outside_body_and_normative_denies(self):
        self.assertIsNone(prove(page("Voltage 800 mV"), box=(0, 100, 200, 130)))
        self.assertIsNone(prove(page("Voltage shall remain 800 mV")))

    def test_partial_ligature_denies(self):
        glyph = dict(x0=0, top=0, x1=3, bottom=9)
        t = NS(
            tuples=[("fi", glyph)],
            as_string="fi",
            line_dir_render="ttb",
            char_dir_render="ltr",
        )
        p = PageText(1, "fi", source_char_map=source_char_map_for_final_text("fi", t))
        self.assertIsNone(prove(p, "f"))

    def test_changed_values_and_coordinates_denied(self):
        a = prove(page("Voltage 800 mV"))
        b = prove(page("Voltage 900 mV"))
        self.assertFalse(equivalent_char_ownership(a, b))
        b = prove(page("Voltage 800 mV"))
        self.assertTrue(equivalent_char_ownership(a, b))
        b["chars"] = [
            (c[0], c[1] + 20, c[2], c[3] + 20, c[4], c[5]) for c in b["chars"]
        ]
        self.assertFalse(equivalent_char_ownership(a, b))

    def test_large_correspondence_refuses_work(self):
        a = {"caption": "F", "chars": [("A", 0, 0, 1, 1, 0)] * 1025, "box": BOX}
        self.assertFalse(equivalent_char_ownership(a, a))

    def test_repeated_char_spatial_ambiguity_denied(self):
        a = prove(page("AA"))
        b = prove(page("AA"))
        b["chars"] = [b["chars"][0], b["chars"][0]]
        self.assertFalse(equivalent_char_ownership(a, b))


if __name__ == "__main__":
    unittest.main()
