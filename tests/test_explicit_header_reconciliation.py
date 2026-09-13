import unittest
from protocol_pdf_diff import compare as m
from protocol_pdf_diff.table_codec import encode_table_field

def rows(labels=('Material Class','Attenuation (dB/mm)','Reach (mm)'),data=(('Medium Loss PCB','0.1','70'),('Low Loss PCB','0.08','88'))):
 return ['表格行: T1 | '+' | '.join(encode_table_field(l,v) for l,v in zip(labels,row)) for row in data]
class Tests(unittest.TestCase):
 def test_complete_semantic_header_retained(self):
  x=m._serialize_explicit_header_numeric_records('Table 1',rows());self.assertEqual('Table 1 Material Class Attenuation (dB/mm) Reach (mm) Medium Loss PCB 0.1 70 Low Loss PCB 0.08 88',x)
 def test_unit_order_change_distinct(self):
  self.assertNotEqual(m._serialize_explicit_header_numeric_records('Table 1',rows()),m._serialize_explicit_header_numeric_records('Table 1',rows(('Material Class','Attenuation (mm/dB)','Reach (mm)'))))
 def test_numeric_cell_split_rejected(self):
  self.assertEqual('',m._serialize_explicit_header_numeric_records('Table 1',rows(data=(('Medium Loss PCB','0.1 70',''),('Low Loss PCB','0.08','88')))))
 def test_schema_drift_rejected(self):
  x=rows();x[1]=x[1].replace('Reach (mm)','Reach (cm)');self.assertEqual('',m._serialize_explicit_header_numeric_records('Table 1',x))
 def test_arbitrary_text_suffix_rejected(self):
  self.assertEqual('',m._serialize_explicit_header_numeric_records('Table 1',rows(data=(('Medium Loss PCB','alpha beta','70'),('Low Loss PCB','alpha','88')))))
 def test_partial_generic_schema_rejected(self):
  self.assertEqual('',m._serialize_explicit_header_numeric_records('Table 1',rows(('Material Class','Column 2','Reach (mm)'))))
if __name__=='__main__':unittest.main()
