"""Unicode table-reference dash regressions."""

from __future__ import annotations

import unittest

from protocol_pdf_diff.compare import (
    _last_table_caption_line,
    _merge_wrapped_lines,
    _split_line_preserving_numbers,
    _standalone_table_reference_number,
    _table_caption_number,
    _text_contains_table_reference,
)


class UnicodeTableReferenceTests(unittest.TestCase):
    """Keep composite table numbers exact across common PDF dash glyphs."""

    DASHES = ("-", "‐", "‑", "‒", "–", "—", "−")

    def test_single_unicode_dash_is_normalized_without_matching_short_number(self) -> None:
        """Every supported glyph denotes one composite number, never its prefix."""

        for dash in self.DASHES:
            caption = f"Table 12{dash}34. Receiver limits"
            sentence = f"See Table 12{dash}34 for receiver limits."
            with self.subTest(dash=repr(dash)):
                self.assertEqual("12-34", _table_caption_number(caption))
                self.assertEqual(
                    "12-34",
                    _standalone_table_reference_number(f"Table 12{dash}34."),
                )
                self.assertTrue(_text_contains_table_reference(sentence, "12-34"))
                self.assertFalse(_text_contains_table_reference(sentence, "12"))

    def test_unicode_dash_with_spacing_is_normalized(self) -> None:
        """Extractor-inserted spacing around one dash does not change identity."""

        for dash in self.DASHES:
            caption = f"Table 12  {dash}  34. Receiver limits"
            with self.subTest(dash=repr(dash)):
                self.assertEqual("12-34", _table_caption_number(caption))
                self.assertTrue(
                    _text_contains_table_reference(
                        f"See Table 12 {dash} 34 for limits.",
                        "12-34",
                    )
                )

    def test_double_or_mixed_dashes_fail_closed(self) -> None:
        """Malformed composites cannot backtrack into a valid short reference."""

        separators = [
            first + second
            for first in self.DASHES
            for second in self.DASHES
        ]
        for separator in separators:
            caption = f"Table 12{separator}34. Receiver limits"
            sentence = f"See Table 12{separator}34 for receiver limits."
            with self.subTest(separator=repr(separator)):
                self.assertEqual("", _table_caption_number(caption))
                self.assertFalse(_text_contains_table_reference(sentence, "12"))
                self.assertFalse(_text_contains_table_reference(sentence, "12-34"))

    def test_existing_unsupported_suffixes_remain_rejected(self) -> None:
        """Unicode support must not reopen letter, dotted, or slash suffixes."""

        for value in (
            "Table 1A. Limits",
            "Table 1.A. Limits",
            "Table 1.1. Limits",
            "Table 1/legacy. Limits",
        ):
            with self.subTest(value=value):
                self.assertEqual("", _table_caption_number(value))
                self.assertFalse(_text_contains_table_reference(value, "1"))
        self.assertEqual("1", _table_caption_number("Table 1. Limits"))

    def test_cross_page_caption_accepts_every_supported_dash(self) -> None:
        """Caption inheritance uses the same composite-number grammar."""

        for dash in self.DASHES:
            caption = f"Table 12{dash}34. Receiver limits"
            with self.subTest(dash=repr(dash)):
                self.assertEqual(
                    caption,
                    _last_table_caption_line(f"preceding prose\n{caption}"),
                )

    def test_wrapped_reference_and_complete_caption_share_unicode_dash_grammar(self) -> None:
        """Line joining and sentence splitting retain every supported composite."""

        for dash in self.DASHES:
            caption = f"Table 12{dash}34. Receiver limits"
            with self.subTest(dash=repr(dash)):
                self.assertEqual(
                    [f"See Table 12{dash}34. Receiver limits apply."],
                    _merge_wrapped_lines(
                        f"See Table 12{dash}\n34. Receiver limits apply."
                    ),
                )
                self.assertEqual(
                    [caption],
                    _split_line_preserving_numbers(caption),
                )


if __name__ == "__main__":
    unittest.main()
