import unittest
from protocol_pdf_diff.pdf_extract import _repair_body_visual_subscript_order as repair
from protocol_pdf_diff.pdf_extract import _looks_like_body_technical_subscript_pair as allowed
from protocol_pdf_diff.text_utils import is_known_engineering_symbol_letter_suffix as global_allowed

def word(text,x0,x1,top=100,bottom=112):
    return dict(text=text,x0=x0,x1=x1,top=top,bottom=bottom)

def sample(base,suffix):
    return [word(base,10,20),word(suffix,20,28,104.8,114.4)]

class BodyLatinSubscriptFamiliesTests(unittest.TestCase):
    def test_all_six_observed_families_rejoin_with_geometry(self):
        for base,suffix in [('t','x'),('p','max'),('MDNEXT','loss'),('f','n'),('f','r'),('f','t')]:
            with self.subTest(base=base,suffix=suffix):
                self.assertEqual(base+suffix,repair(base+' '+suffix,sample(base,suffix)))
    def test_changed_value_is_preserved(self):
        for value in ['0.02','0.03']:
            words=sample('t','x')+[word(value,40,70)]
            self.assertEqual('tx '+value,repair('t x '+value,words))
        self.assertNotEqual(repair('t x',sample('t','x')),repair('t y',sample('t','y')))
    def test_mode_a_is_not_identifier_repair(self):
        self.assertEqual('Mode A',repair('Mode A',sample('Mode','A')))
    def test_same_baseline_does_not_rejoin(self):
        self.assertEqual('t x',repair('t x',[word('t',10,20),word('x',20,28)]))
    def test_raised_suffix_does_not_rejoin(self):
        self.assertEqual('t x',repair('t x',[word('t',10,20),word('x',20,28,96,105.6)]))
    def test_missing_coordinate_does_not_rejoin(self):
        words=sample('t','x');del words[1]['x0']
        self.assertEqual('t x',repair('t x',words))
    def test_missing_character_coverage_does_not_rejoin(self):
        self.assertEqual('t x extra',repair('t x extra',sample('t','x')))
    def test_ambiguous_base_does_not_rejoin(self):
        words=[word('t',10,20),word('t',20.5,23),word('x',23.5,29,104.8,114.4)]
        self.assertEqual('t t\nx',repair('t t\nx',words))
    def test_repeated_source_line_remains(self):
        words=sample('t','x')+[word('t',10,20,140,152)]
        self.assertEqual('t\nx\nt',repair('t\nx\nt',words))
    def test_pua_is_not_newly_authorized(self):
        self.assertFalse(allowed('\uf073','e'))
        self.assertEqual('\uf073 e',repair('\uf073 e',sample('\uf073','e')))
    def test_global_text_only_allowlist_is_unchanged(self):
        for base,suffix in [('t','x'),('p','max'),('MDNEXT','loss'),('f','n'),('f','r'),('f','t')]:
            self.assertFalse(global_allowed(base,suffix))

if __name__=='__main__':unittest.main()
