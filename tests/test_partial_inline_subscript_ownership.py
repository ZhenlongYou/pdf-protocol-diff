import unittest
from protocol_pdf_diff.pdf_extract import _repair_body_visual_subscript_order as repair


def word(text, x, y, size=12):
    return dict(text=text, x0=x, x1=x+len(text)*5, top=y, bottom=y+size)


class PartialInlineSubscriptOwnershipTests(unittest.TestCase):
    def test_single_raw_line_preserves_unowned_lowered_word(self):
        words = [word('f',0,0),word('b',5,4,9.6),word('untouched',30,0),word('ILmin',90,4,9.6)]
        self.assertEqual('fb untouched ILmin',repair('f b untouched ILmin',words))
        self.assertEqual('f b ILmin untouched',repair('f b ILmin untouched',words))
        self.assertEqual('f b untouched ILmin',repair('f b untouched ILmin',words[:-1]))

    def test_lowered_scalar_change_remains(self):
        outputs=[]
        for value in ['800','900']:
            words=[word('C',0,0),word('-1',5,4,9.6),word('value',30,0),word(value,80,4,9.6)]
            output=repair('C -1 value '+value,words)
            self.assertEqual('C-1 value '+value,output)
            outputs.append(output)
        self.assertNotEqual(*outputs)

    def test_multiline_does_not_cross_unowned_lowered_word(self):
        words=[word('f',0,0),word('b',5,4,9.6),word('untouched',30,0),word('ILmin',90,4,9.6)]
        raw='f\nb\nuntouched ILmin'
        self.assertEqual(raw,repair(raw,words))

    def test_duplicate_base_still_blocks_partial_inline(self):
        words=[word('f',0,0),word('b',5,4,9.6),word('untouched',30,0),word('ILmin',90,4,9.6),word('f',0,30),word('untouched',30,30)]
        raw='f b untouched ILmin\nf untouched'
        self.assertEqual(raw,repair(raw,words))
