import unittest
from copy import deepcopy
from protocol_pdf_diff import pdf_extract as e

def fixture():
 rows=[['Characteristic','Symbol','Condition','MAX'],['First','A','See Note1','1'],['Second','B',None,'2']]
 bounds=[[(c*100,0,(c+1)*100,10) for c in range(4)],[(0,10,100,20),(100,10,200,20),(200,10,300,30),(300,10,400,20)],[(0,20,100,30),(100,20,200,30),None,(300,20,400,30)]]
 words=[]
 for r,br in zip(rows,bounds):
  wr=[]
  for text,box in zip(r,br):
   wr.append([] if not text else [dict(text=text,x0=box[0]+1,x1=box[0]+50,top=box[1]+1,bottom=box[1]+5)])
  words.append(wr)
 return rows,bounds,words

class MergedCellTests(unittest.TestCase):
 def test_unique_owner(self):
  self.assertEqual({(2,2):1},e._merged_cell_display_owners(*fixture()))
 def test_competing_owner_cells_rejected(self):
  r,b,w=fixture();r.insert(2,deepcopy(r[1]));b.insert(2,deepcopy(b[1]));w.insert(2,deepcopy(w[1]))
  self.assertNotIn((3,2),e._merged_cell_display_owners(r,b,w))
 def test_missing_bounds(self):
  r,b,w=fixture();self.assertEqual({},e._merged_cell_display_owners(r,None,w))
 def test_owner_does_not_cover_target(self):
  r,b,w=fixture();b[1][2]=(200,10,300,20);self.assertEqual({},e._merged_cell_display_owners(r,b,w))
 def test_explicit_condition_never_overwritten(self):
  r,b,w=fixture();r[2][2]='See Note2';self.assertEqual({},e._merged_cell_display_owners(r,b,w))
 def test_real_empty_cell_not_merged(self):
  r,b,w=fixture();b[2][2]=(200,20,300,30);self.assertEqual({},e._merged_cell_display_owners(r,b,w))
 def test_changed_source_note_without_word_evidence_rejected(self):
  r,b,w=fixture();r[1][2]='See Note2';self.assertEqual({},e._merged_cell_display_owners(r,b,w))
 def test_missing_source_word_rejected(self):
  r,b,w=fixture();w[1][2]=[];self.assertEqual({},e._merged_cell_display_owners(r,b,w))
 def test_numeric_column_not_inherited(self):
  r,b,w=fixture();r[2][3]=None;b[1][3]=(300,10,400,30);b[2][3]=None;w[2][3]=[]
  self.assertNotIn((2,3),e._merged_cell_display_owners(r,b,w))
 def test_symbol_source_change_not_fabricated(self):
  r,b,w=fixture();r[2][1]=None;b[1][1]=(100,10,200,30);b[2][1]=None;w[2][1]=[];r[1][1]='C'
  self.assertNotIn((2,1),e._merged_cell_display_owners(r,b,w))
 def test_display_keeps_changed_number(self):
  r,b,w=fixture();r[2][3]='3';w[2][3][0]['text']='3'
  lines,*_=e._table_lines_from_rows_with_data_evidence(r,1,cell_word_rows=w,cell_bounds_rows=b)
  self.assertIn('MAX=3',lines[-1]);self.assertIn('Condition=See Note1',lines[-1])

def header_fixture():
 r=[['Coefficients','Normalized Amplitude',None,'Step Size (%)'],[None,'Min (%)','Max (%)',None],['c(0)','40','100','0.2']]
 b=[[(0,0,100,20),(100,0,300,10),None,(300,0,400,20)],
    [None,(100,10,200,20),(200,10,300,20),None],
    [(0,20,100,30),(100,20,200,30),(200,20,300,30),(300,20,400,30)]]
 w=[[[] if v is None else [dict(text=v,x0=box[0]+1,x1=box[2]-1,top=box[1]+1,bottom=box[1]+5)] for v,box in zip(rr,bb)] for rr,bb in zip(r,b)]
 return r,b,w

class GroupedHeaderTests(unittest.TestCase):
 def lines(self,r,b,w):
  return e._table_lines_from_rows_with_data_evidence(r,1,cell_word_rows=w,cell_bounds_rows=b)[0]
 def test_proven_parent_leaf_labels(self):
  lines=self.lines(*header_fixture());self.assertEqual(1,len(lines))
  self.assertIn('Normalized Amplitude / Min (%)=40',lines[0])
  self.assertIn('Normalized Amplitude / Max (%)=100',lines[0])
 def test_missing_bounds_keeps_raw_header(self):
  r,b,w=header_fixture();self.assertEqual(3,len(self.lines(r,None,w)))
 def test_parent_does_not_own_max(self):
  r,b,w=header_fixture();b[0][1]=(100,0,200,10)
  self.assertEqual(3,len(self.lines(r,b,w)))
 def test_reversed_min_max_labels_preserved(self):
  r,b,w=header_fixture();r[1][1],r[1][2]=r[1][2],r[1][1]
  w[1][1][0]['text']='Max (%)';w[1][2][0]['text']='Min (%)'
  lines=self.lines(r,b,w);self.assertEqual(3,len(lines))
  self.assertIn('Column 2=Max (%)',lines[1])
 def test_short_geometry_fails_closed(self):
  r,b,w=header_fixture();b[1]=[]
  self.assertEqual(3,len(self.lines(r,b,w)))
 def test_numeric_parent_is_not_dropped(self):
  r,b,w=header_fixture();r[0][1]='100';w[0][1][0]['text']='100'
  self.assertEqual(3,len(self.lines(r,b,w)))
 def test_changed_min_value_retained(self):
  r,b,w=header_fixture();r[2][1]='41';w[2][1][0]['text']='41'
  self.assertIn('Min (%)=41',self.lines(r,b,w)[0])
 def test_changed_unit_is_preserved_not_guessed(self):
  r,b,w=header_fixture();r[1][1]='Min (dB)';w[1][1][0]['text']='Min (dB)'
  self.assertIn('Min (dB)', ' '.join(self.lines(r,b,w)))

class BadGeometryTests(unittest.TestCase):
 def test_bad_owner_and_target_boxes_reject(self):
  for loc in [(0,2),(1,2),(2,0)]:
   for bad in [(200,10,300,float('inf')),(200,10,300,float('nan')),(200,10,200,20),(300,10,200,20),(200,10,300)]:
    with self.subTest(loc=loc,bad=bad):
     r,b,w=fixture();b[loc[0]][loc[1]]=bad
     self.assertEqual({},e._merged_cell_display_owners(r,b,w))
 def test_missing_word_coordinate_rejects(self):
  r,b,w=fixture();del w[1][2][0]['x0']
  self.assertEqual({},e._merged_cell_display_owners(r,b,w))
 def test_header_bad_parent_bbox_keeps_raw_rows(self):
  for bad in [(0,0,100,float('inf')),(0,0,0,20),(0,0,100)]:
   r,b,w=header_fixture();b[0][0]=bad
   lines=e._table_lines_from_rows_with_data_evidence(r,1,cell_word_rows=w,cell_bounds_rows=b)[0]
   self.assertEqual(3,len(lines))
 def test_header_missing_word_coordinate_keeps_raw_rows(self):
  r,b,w=header_fixture();del w[0][0][0]['x0']
  lines=e._table_lines_from_rows_with_data_evidence(r,1,cell_word_rows=w,cell_bounds_rows=b)[0]
  self.assertEqual(3,len(lines))
 def test_header_nonfinite_word_rejects(self):
  r,b,w=header_fixture();w[0][0][0]['bottom']=float('inf')
  self.assertFalse(e._valid_merged_table_geometry(r,b,w))

if __name__=='__main__':unittest.main()
