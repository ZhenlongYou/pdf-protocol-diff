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
