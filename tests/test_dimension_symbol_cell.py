"""Native three-word typography fixture; no desktop PDFs are required."""
import copy
import json
from pathlib import Path
import unittest

from protocol_pdf_diff.pdf_extract import (
    _rebuild_dimension_symbol_cell,
    _rejoin_table_cell_subscript_lines,
)


class DimensionSymbolCellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "content-correspondence"
             / "dimension-symbol-cell.json").read_text(encoding="utf-8")
        )

    def sample(self):
        return self.fixture["raw"].splitlines(), copy.deepcopy(self.fixture["words"])

    def test_real_native_cell_preserves_unit(self):
        lines, words = self.sample()
        self.assertEqual("zp (mm)", _rebuild_dimension_symbol_cell(lines, words))
        self.assertEqual("zp (mm)", _rejoin_table_cell_subscript_lines("\n".join(lines), words))

    def test_base_suffix_and_unit_changes_remain_distinct(self):
        for index, before, after, expected in (
            (0, "z", "x", "xp (mm)"),
            (1, "p", "q", "zq (mm)"),
            (2, "(mm)", "(cm)", "zp (cm)"),
        ):
            with self.subTest(changed_word=index):
                lines, words = self.sample()
                words[index]["text"] = after
                line_index = 1 if index == 1 else 0
                lines[line_index] = lines[line_index].replace(before, after)
                actual = _rebuild_dimension_symbol_cell(lines, words)
                self.assertEqual(expected, actual)
                self.assertNotEqual("zp (mm)", actual)

    def test_same_baseline_is_not_subscript(self):
        lines, words = self.sample()
        words[1].update(top=words[0]["top"], bottom=words[0]["bottom"], size=words[0]["size"])
        self.assertIsNone(_rebuild_dimension_symbol_cell(lines, words))

    def test_missing_words_and_coordinates_rejected_by_new_helper(self):
        # Legacy wrapper fallback assumes valid words. Its pre-existing malformed
        # coordinate behavior is not asserted as a new guarantee here.
        for index in range(3):
            lines, words = self.sample()
            del words[index]
            self.assertIsNone(_rebuild_dimension_symbol_cell(lines, words))
        for key in ("x0", "x1", "top", "bottom", "size"):
            lines, words = self.sample()
            del words[1][key]
            self.assertIsNone(_rebuild_dimension_symbol_cell(lines, words))

    def test_nonfinite_geometry_rejected(self):
        for index in range(3):
            for key in ("x0", "x1", "top", "bottom", "size"):
                for value in (float("nan"), float("inf"), float("-inf")):
                    with self.subTest(word=index, key=key, value=value):
                        lines, words = self.sample()
                        words[index][key] = value
                        self.assertIsNone(_rebuild_dimension_symbol_cell(lines, words))

    def test_complete_source_order_and_font_required(self):
        for variant in ("reversed_words", "wrong_font", "wrong_raw_order", "extra_text"):
            with self.subTest(variant=variant):
                lines, words = self.sample()
                if variant == "reversed_words":
                    words[0], words[1] = words[1], words[0]
                elif variant == "wrong_font":
                    words[1]["fontname"] = "DifferentFont"
                elif variant == "wrong_raw_order":
                    lines[0] = "(mm) z"
                else:
                    lines[1] += " condition"
                self.assertIsNone(_rebuild_dimension_symbol_cell(lines, words))


if __name__ == "__main__":
    unittest.main()
