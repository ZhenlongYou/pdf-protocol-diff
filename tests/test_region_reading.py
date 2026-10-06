"""跨栏标题与正文分栏的受控反例；同时验证文字和来源顺序。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.evidence_alignment import evidence_from_extraction
from tests.test_parser_routing import _TwoColumnNativePage, _OnePagePdf, _BorderlessTwoColumnTablePage


class FullWidthHeadingPage(_TwoColumnNativePage):
    def extract_words(self, **kwargs):
        return [{"text": "A full width title across both columns", "x0": 60, "x1": 550,
                 "top": 55, "bottom": 70}, *super().extract_words(**kwargs)]


class RegionReadingTests(unittest.TestCase):
    def extract(self, page):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "region.pdf"
            source.write_bytes(b"%PDF-1.4\n%%EOF\n")  # 页面几何由受控输入提供，抽取路径真实运行。
            with patch("pdfplumber.open", return_value=_OnePagePdf(page)):
                return extract_pdf_text(source)

    def test_full_width_heading_precedes_complete_left_and_right_columns(self):
        result = self.extract(FullWidthHeadingPage())
        page = result.pages[0]
        self.assertTrue(page.text.startswith("A full width title"))
        self.assertLess(page.text.index("Final left paragraph"), page.text.index("This condition paragraph"))
        self.assertEqual(page.text, "\n".join(b.text for b in page.comparison_blocks))
        self.assertTrue(page.layout_risk)  # 改善顺序并不认证全部语义归属。
        evidence = evidence_from_extraction(result)
        self.assertNotIn("source_coordinates_missing", evidence.coverage_reasons)
        self.assertTrue(all(unit.bbox is not None for unit in evidence.units))
        self.assertEqual(len(FullWidthHeadingPage().extract_words()),
                         sum(len(block.word_boxes) for block in page.comparison_blocks))

    def test_borderless_parameter_values_stay_with_their_rows(self):
        page = self.extract(_BorderlessTwoColumnTablePage()).pages[0]
        self.assertFalse(page.comparison_blocks)  # 表头和数值网格不足以授权列优先重排。
        self.assertLess(page.text.index("3.3 Volts"), page.text.index("Timing window"))


class NarrowColumnPage(_TwoColumnNativePage):
    width = 612
    bbox = (0, 0, 612, 792)
    table = False

    def extract_words(self, **kwargs):
        words = []
        for side, start in [('Left', 38), ('Right', 301)]:
            for i in range(12):
                text = (f'{side} continuous paragraph keeps the complete source words'
                        if i % 3 != 2 else f'{side} final sentence records the operating conditions.')
                if self.table and side == 'Right' and i % 4 == 0:
                    text = '3.3 V describes the permitted operating conditions here'
                tokens = text.split()
                step = 251 / sum(len(t)+1 for t in tokens)
                x = start
                for token in tokens:
                    width = len(token)*step
                    words.append(dict(text=token,x0=x,x1=x+width,top=160+i*12+(5 if side=='Right' and not self.table else 0),
                                      bottom=170+i*12+(5 if side=='Right' and not self.table else 0),fontname='TestSerif',size=9))
                    x += width+step
        return words

    def extract_text(self, **kwargs):
        from protocol_pdf_diff.pdf_extract import _visual_word_lines, _words_to_visual_line
        return '\n'.join(_words_to_visual_line(line) for line in _visual_word_lines(self.extract_words()))


class NarrowColumnReadingTests(unittest.TestCase):
    extract = RegionReadingTests.extract

    def test_staggered_narrow_prose_preserves_styles_and_reads_each_column(self):
        page=self.extract(NarrowColumnPage()).pages[0]
        self.assertTrue(page.comparison_blocks)
        self.assertGreater(page.text.index('Right continuous'),page.text.rindex('Left final'))
        self.assertTrue(all(b.font_names==('TestSerif',) for b in page.comparison_blocks))
        self.assertTrue(all(style==('TestSerif',9.0) for b in page.comparison_blocks for style in b.word_styles))

    def test_wrapped_value_cells_are_not_reordered_as_paragraphs(self):
        original=NarrowColumnPage();original.table=True
        page=self.extract(original).pages[0]
        self.assertFalse(page.comparison_blocks)
        self.assertLess(page.text.index('3.3 V'),page.text.rindex('Left final'))
