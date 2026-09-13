import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from protocol_pdf_diff.source_typography import source_superscript_receipts, restore_superscript_display


def make_page(*, baseline=False, footnote=False, missing=False):
    texts=['The','value','is','note' if footnote else '10','20','today.']
    words=[];styles=[];x=0
    for i,text in enumerate(texts):
        raised=i==4 and not baseline
        size=9.0 if raised else 12.0;top=97.2 if raised else 100.0
        words.append((text,x,top,x+len(text)*6,top+size));styles.append(('Arial',size))
        x+=len(text)*6+(0 if i==3 else 3)
    raw='The value is '+('note' if footnote else '10')+'20 today.'
    block=NS(text=' '.join(texts),word_boxes=tuple(words),word_styles=() if missing else tuple(styles))
    return NS(page_number=1,text=raw,ocr_used=False,blocks=(block,))


class SourceTypographyTests(unittest.TestCase):
    def display(self,page,value=None,pages=None):
        receipts=source_superscript_receipts(NS(pages=pages or [page]))
        return restore_superscript_display(value or page.text,NS(start_page=1,end_page=900),receipts)

    def test_real_two_pages_preserve_raised_digits(self):
        data=json.loads((Path(__file__).parent/'fixtures/content-correspondence/source-superscripts.json').read_text())
        for item in data:
            page=NS(**{**item,'blocks':[NS(**b) for b in item['blocks']]})
            with self.subTest(page=page.page_number):
                display=self.display(page)
                self.assertIn('10²⁰',display);self.assertIn('10¹²',display)
                self.assertIn('1020',page.text);self.assertIn('1012',page.text)

    def test_normal_1020_stays_baseline(self):
        page=make_page(baseline=True)
        self.assertEqual(self.display(page),page.text)

    def test_footnote_preserves_typography_without_power_syntax(self):
        display=self.display(make_page(footnote=True))
        self.assertIn('note²⁰',display);self.assertNotIn('^',display)

    def test_missing_styles_fail_closed(self):
        page=make_page(missing=True)
        self.assertEqual(self.display(page),page.text)

    def test_repeated_source_or_display_cannot_borrow_evidence(self):
        page=make_page()
        self.assertEqual(self.display(page,pages=[page,page]),page.text)
        unknown=NS(page_number=2,text=page.text,ocr_used=False,blocks=())
        self.assertEqual(self.display(page,pages=[page,unknown]),page.text)
        repeated=page.text+' '+page.text
        self.assertEqual(self.display(page,repeated),repeated)

    def test_ocr_or_wrong_page_cannot_authorize(self):
        page=make_page();page.ocr_used=True
        self.assertEqual(self.display(page),page.text)
        page.ocr_used=False
        receipts=source_superscript_receipts(NS(pages=[page]))
        self.assertEqual(restore_superscript_display(page.text,NS(start_page=2,end_page=3),receipts),page.text)

if __name__=='__main__': unittest.main()
