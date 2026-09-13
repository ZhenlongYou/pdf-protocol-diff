import unittest, copy
from protocol_pdf_diff import pdf_extract as e
from protocol_pdf_diff import reporting as r
from protocol_pdf_diff.models import TableVisual

def fixture(header=None):
 rows=[header or ['Material class','Loss (dB/mm)','Reach (mm)'],['Copper','0.1','70'],['Glass','0.08','88']]
 bounds=[[(i*100,j*20,(i+1)*100,(j+1)*20) for i in range(3)] for j in range(3)]
 words=[[[dict(text=c,fontname='Arial-Bold' if j==0 else 'Arial',size=10,x0=i*100+2,x1=(i+1)*100-2,top=j*20+2,bottom=(j+1)*20-2)] for i,c in enumerate(row)] for j,row in enumerate(rows)]
 return rows,words,bounds

class Tests(unittest.TestCase):
 def yes(self,f):return e._geometry_proven_single_header(*f)
 def test_physical_dimension_header(self):self.assertTrue(self.yes(fixture()))
 def test_missing_geometry(self):
  rows,w,b=fixture();self.assertFalse(self.yes((rows,w,None)))
 def test_missing_font(self):
  f=fixture();f[1][0][1][0].pop('fontname');self.assertFalse(self.yes(f))
 def test_regular_first_row(self):
  f=fixture();f[1][0][0][0]['fontname']='Arial';self.assertFalse(self.yes(f))
 def test_whole_table_bold(self):
  f=fixture();f[1][1][1][0]['fontname']='Arial-Bold';self.assertFalse(self.yes(f))
 def test_missing_covered_header_word(self):
  f=fixture();f[1][0][1]=[];self.assertFalse(self.yes(f))
 def test_merged_parent_not_single_header(self):
  f=fixture();f[2][0][1]=None;self.assertFalse(self.yes(f))
 def test_shifted_columns(self):
  f=fixture();f[2][1][1]=(110,20,210,40);self.assertFalse(self.yes(f))
 def test_numeric_first_record(self):self.assertFalse(self.yes(fixture(['Minimum voltage','5','V'])))
 def test_arbitrary_bold_labels(self):self.assertFalse(self.yes(fixture(['Copper','fast','long'])))
 def test_header_units_change_remains(self):
  def table(h):
   rows,w,b=fixture(h);lines=e._table_lines_from_rows_with_data_evidence(rows,1,cell_word_rows=w,cell_bounds_rows=b)[0]
   return TableVisual(1,1,'Table 1', (0,0,300,60),'',lines,'grid',row_alignment_reliable=True)
  c=r._table_row_changes((table(['Material class','Loss (dB/mm)','Reach (mm)']),),(table(['Material class','Loss (dB/mm)','Reach (cm)']),))
  text=' '.join(x.old_value+x.new_value for x in c)
  self.assertIn('Reach (mm)',text);self.assertIn('Reach (cm)',text);self.assertIn('70',text)
 def test_note_marker_change_remains(self):
  a=fixture(['Material Note1','Loss (dB/mm)','Reach (mm)']);b=fixture(['Material Note2','Loss (dB/mm)','Reach (mm)'])
  def table(f):return TableVisual(1,1,'Table 1',(0,0,300,60),'',e._table_lines_from_rows_with_data_evidence(f[0],1,cell_word_rows=f[1],cell_bounds_rows=f[2])[0],'grid',row_alignment_reliable=True)
  changes=r._table_row_changes((table(a),),(table(b),));text=' '.join(x.old_value+x.new_value for x in changes)
  self.assertIn('Note1',text);self.assertIn('Note2',text)
 def test_numeric_payload_variants(self):
  for value in ['800 mV','-800 mV','0.1dB/mm','Typical 800 mV']:
   f=fixture(['Voltage (mV)',value,'Range (mm)'])
   self.assertFalse(self.yes(f),value)
 def test_numeric_payload_with_unit_suffix(self):
  self.assertFalse(self.yes(fixture(['Voltage (mV)','Typical 800 mV (mV)','Range (mm)'])))
 def test_nonnumeric_measurement_outcome(self):
  self.assertFalse(self.yes(fixture(['Frequency (Hz)','Not measured','Unavailable'])))
 def test_ordered_unit_source_conflict(self):
  f=fixture();f[1][0][1][0]['text']='Loss (mm/dB)';self.assertFalse(self.yes(f))
 def test_quantified_header_condition(self):
  self.assertTrue(self.yes(fixture(['Material Note1','Loss (dB/mm)','Reach at 7 dB (mm)'])))
 def test_normal_font_numeric_payload(self):
  f=fixture(['Voltage (mV)','Typical 800 mV','Range (mm)'])
  f[1][0][1]=[dict(text='Typical',fontname='Arial-Bold',size=10,x0=102,x1=130,top=2,bottom=18),dict(text='800',fontname='Arial',size=10,x0=132,x1=150,top=2,bottom=18),dict(text='mV',fontname='Arial-Bold',size=10,x0=152,x1=180,top=2,bottom=18)]
  self.assertFalse(self.yes(f))
 def test_malformed_boxes_fail_closed_all_rows(self):
  for j in range(3):
   for bad in [(0,0,float('inf'),20),(0,0,float('nan'),20),(1,0,0,20),(0,1,100,0),(0,0,0,20),(0,0,100),None]:
    f=fixture();f[2][j][0]=bad
    self.assertFalse(self.yes(f),(j,bad))
 def test_malformed_word_coordinates_fail_closed_all_rows(self):
  for j in range(3):
   for key,bad in [('x0',None),('x0',float('nan')),('x1',float('inf')),('top',float('-inf')),('bottom',-1)]:
    f=fixture();f[1][j][0][0][key]=bad
    self.assertFalse(self.yes(f),(j,key,bad))
   f=fixture();f[1][j][0][0].pop('x0');self.assertFalse(self.yes(f))
if __name__=='__main__':unittest.main()
