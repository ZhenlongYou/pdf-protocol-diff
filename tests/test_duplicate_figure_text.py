import unittest
from pathlib import Path

from protocol_pdf_diff.pdf_extract import extract_pdf_text

SOURCE = Path("/Users/mac/Desktop/OIF-CEI-5.1.pdf")


@unittest.skipUnless(SOURCE.is_file(), "local OIF source PDF is unavailable")
class DuplicateFigureTextTests(unittest.TestCase):
    def test_real_page_209_deduplicates_overpainted_figure_labels(self) -> None:
        """Figure 8-4 labels are painted three times at the same physical glyphs."""

        extraction = extract_pdf_text(SOURCE, start_page=209, end_page=209)
        text = extraction.pages[0].text

        self.assertNotIn("CCoommppoonneenntt", text)
        self.assertNotIn("EEddggee", text)
        self.assertEqual(2, text.count("Component"))
        self.assertEqual(2, text.count("Edge"))

    def test_real_page_209_filters_header_and_right_line_numbers(self) -> None:
        """Publication furniture must not become page-content differences."""

        for pdf_path, page_number, revision in (
            (SOURCE, 209, "05.1"),
            (Path("/Users/mac/Desktop/OIF-CEI-05.3.pdf"), 213, "05.3"),
        ):
            with self.subTest(pdf=pdf_path.name):
                extraction = extract_pdf_text(
                    pdf_path,
                    start_page=page_number,
                    end_page=page_number,
                )
                text = extraction.pages[0].text
                self.assertNotIn(f"Implementation Agreement OIF-CEI-{revision}", text)
                self.assertNotIn("D 32", text)
                self.assertNotIn("* 33", text)
                self.assertNotIn(" 209", text)
                self.assertNotIn(" 213", text)
