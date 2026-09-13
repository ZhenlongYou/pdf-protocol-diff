import unittest
from protocol_pdf_diff.pdf_extract import _repair_body_visual_subscript_order as repair,_looks_like_body_technical_subscript_pair as allowed
from test_body_latin_subscript_families import sample,word
PAIRS=[('T','E'),('T','I'),('T','fx'),('N','bx')]
class FourLatinTests(unittest.TestCase):
 def test_four_positive(self):
  for a,b in PAIRS:self.assertEqual(a+b,repair(a+' '+b,sample(a,b)))
 def test_ordinary_size_reject(self):
  for a,b in PAIRS:self.assertEqual(a+' '+b,repair(a+' '+b,[word(a,10,20),word(b,20,28)]))
 def test_incomplete_words_reject(self):
  for a,b in PAIRS:self.assertEqual(a+' '+b+' extra',repair(a+' '+b+' extra',sample(a,b)))
 def test_duplicate_line_reject(self):
  for a,b in PAIRS:self.assertEqual(a+'\n'+b+'\n'+a,repair(a+'\n'+b+'\n'+a,sample(a,b)+[word(a,10,20,140,152)]))
 def test_numbers_preserved(self):
  for a,b in PAIRS:
   for v in ['0','1','900mV']:
    self.assertEqual(a+b+' '+v,repair(a+' '+b+' '+v,sample(a,b)+[word(v,40,80)]))
 def test_changed_letter_not_equal(self):
  self.assertNotEqual(repair('T E',sample('T','E')),repair('T I',sample('T','I')))
  self.assertNotEqual(repair('T fx',sample('T','fx')),repair('N bx',sample('N','bx')))
 def test_ordinary_prose_not_admitted(self):
  self.assertEqual('Mode E',repair('Mode E',sample('Mode','E')))
 def test_C84_not_added(self):self.assertFalse(allowed('f','CRU'))
 def test_unknown_pua_not_added(self):self.assertFalse(allowed('\ue001','E'))
