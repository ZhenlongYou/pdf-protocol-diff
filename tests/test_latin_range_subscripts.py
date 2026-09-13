import unittest
from protocol_pdf_diff.pdf_extract import _repair_body_visual_subscript_order as repair
PAIRS=[('f','ILmin'),('f','ILmax'),('IL','TC'),('m','TC'),('b','TC'),('FOM','ILD')]
def words(a,b):
 return [dict(text=a,x0=10,x1=20,top=100,bottom=112),dict(text=b,x0=20,x1=28,top=104.8,bottom=114.4)]
class LatinRangeTests(unittest.TestCase):
 def test_positive(self):
  for a,b in PAIRS:self.assertEqual(a+b,repair(a+' '+b,words(a,b)))
 def test_numbers_conditions_preserved(self):
  for a,b in PAIRS:
   for v in ['800mV','900mV','10MHz','11MHz','shall remain','must stop']:
    self.assertEqual(a+b+' '+v,repair(a+' '+b+' '+v,words(a,b)+[dict(text=v,x0=40,x1=100,top=100,bottom=112)]))
 def test_same_baseline(self):
  for a,b in PAIRS:
   w=words(a,b);w[1].update(top=100,bottom=112);self.assertEqual(a+' '+b,repair(a+' '+b,w))
 def test_incomplete_source(self):
  for a,b in PAIRS:self.assertEqual(a+' '+b+' missing',repair(a+' '+b+' missing',words(a,b)))
 def test_missing_geometry(self):
  for a,b in PAIRS:
   w=words(a,b);del w[1]['x0'];self.assertEqual(a+' '+b,repair(a+' '+b,w))
 def test_nonfinite_geometry(self):
  for a,b in PAIRS:
   w=words(a,b);w[1]['top']=float('nan');self.assertEqual(a+' '+b,repair(a+' '+b,w))
 def test_real_identifier_changes_remain(self):
  self.assertNotEqual(repair('f ILmin',words('f','ILmin')),repair('f ILmax',words('f','ILmax')))
 def test_no_ordinary_word_admission(self):self.assertEqual('Mode TC',repair('Mode TC',words('Mode','TC')))
 def test_duplicate_line(self):
  for a,b in PAIRS:
   raw=a+'\n'+b+'\n'+a;w=words(a,b)+[dict(text=a,x0=10,x1=20,top=140,bottom=152)];self.assertEqual(raw,repair(raw,w))
