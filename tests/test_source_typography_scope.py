import copy
import json
from pathlib import Path
import unittest
from types import SimpleNamespace as NS
from test_source_typography import make_page
from protocol_pdf_diff.source_typography import source_superscript_receipts, restore_superscript_display


def display(value, pages, first=1, last=1):
    return restore_superscript_display(value, NS(start_page=first,end_page=last),
                                      source_superscript_receipts(NS(pages=pages)))


class TypographyScopeTests(unittest.TestCase):
    def test_ninth_report_actual_snippets_and_native_blocks(self):
        data=json.loads((Path(__file__).parent/'fixtures/source-typography-full-context.json').read_text())
        pages=[NS(**{**item['page'],'blocks':[NS(**b) for b in item['page']['blocks']]})
               for item in data['items']]
        # An unrelated same-text page intentionally has no transferable styles.
        pages.append(NS(page_number=524,text=pages[0].text,blocks=(),ocr_used=False))
        for item in data['items']:
            pn=item['page']['page_number']
            result=display(item['added_snippets'][0],pages,pn,pn)
            self.assertIn('10²⁰',result)
            self.assertIn('10¹²',result)

    def test_other_section_identical_line_does_not_destroy_local_proof(self):
        a=make_page(); b=copy.deepcopy(a); b.page_number=9
        self.assertIn('10²⁰',display(a.text,[a,b]))
        self.assertEqual(a.text,display(a.text,[a,b],1,9))

    def test_unstyled_occurrence_in_same_section_remains_a_blocker(self):
        a=make_page(); b=make_page(missing=True); b.page_number=2
        self.assertEqual(a.text,display(a.text,[a,b],1,2))
        self.assertIn('10²⁰',display(a.text,[a,b]))

    def test_unknown_blockless_occurrence_in_section_remains_a_blocker(self):
        a=make_page(); b=NS(page_number=2,text=a.text,blocks=(),ocr_used=False)
        self.assertEqual(a.text,display(a.text,[a,b],1,2))

    def test_duplicate_in_one_page_cannot_borrow_first_style(self):
        a=make_page(); a.text += ' '+a.text
        self.assertEqual(a.text,display(a.text,[a]))

    def test_wrong_page_missing_style_and_changed_digit_fail_closed(self):
        a=make_page()
        self.assertEqual(a.text,display(a.text,[a],2,2))
        b=make_page(missing=True)
        self.assertEqual(b.text,display(b.text,[b]))
        changed=a.text.replace('1020','1021')
        self.assertEqual(changed,display(changed,[a]))

    def test_footnote_style_cannot_be_borrowed_for_a_numeric_power(self):
        a=make_page(footnote=True)
        numeric=a.text.replace('note20','1020')
        self.assertEqual(numeric,display(numeric,[a]))
        self.assertIn('note²⁰',display(a.text,[a]))

    def test_only_proven_gutter_box_may_remove_native_prefix(self):
        a=make_page(); block=a.blocks[0]
        block.text='22 '+block.text
        block.word_boxes=(('22',-30.,100.,-18.,112.),*block.word_boxes)
        block.word_styles=(('Arial',12.),*block.word_styles)
        self.assertEqual(a.text,display(a.text,[a]))
        a.visual_noise_bboxes=((-30.,100.,-18.,112.),)
        self.assertIn('10²⁰',display(a.text,[a]))


if __name__=='__main__': unittest.main()
