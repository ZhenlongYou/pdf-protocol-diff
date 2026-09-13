import base64,io,unittest,csv
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import patch
from tempfile import TemporaryDirectory
from pathlib import Path
from PIL import Image
from protocol_pdf_diff.models import PhysicalTableRow,TableVisual
from protocol_pdf_diff import physical_table_rows as p

class SingleSymbolTests(unittest.TestCase):
 def setUp(self):
  self.cells=('Equalizer\nMinimum value\nMaximum value\nStep size','c(1)','-0.18\n0\n0.02','—\n—\n—')
  self.boxes=tuple((i*100,0,(i+1)*100,100) for i in range(4))
  self.words=tuple(tuple((word,1+i*100,1+j*10,90+i*100,8+j*10) for j,word in enumerate(cell.splitlines())) for i,cell in enumerate(self.cells))
  self.row=PhysicalTableRow('old',self.cells,(0,0,400,100),self.boxes,self.words)
  image=io.BytesIO();Image.new('RGB',(8,8)).save(image,format='PNG');uri='data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()
  self.old=TableVisual(1,1,'Table 1',(0,0,400,100),uri,[],'',physical_rows=(self.row,))
  self.new=replace(self.old,page_number=2,physical_rows=(replace(self.row,row_id='new'),))
 def render(self,new=None):
  return p.render_physical_appendix([NS(old_tables=(self.old,),new_tables=(new or self.new,))])
 def test_whole_four_cells_and_newlines_preserved(self):
  html,payload,receipt=self.render();self.assertEqual(2,len(receipt));self.assertEqual(self.cells,payload[0]['old']['cells']);self.assertIn(self.cells[0],html);self.assertEqual(2,html.count('<img'))
 def test_missing_image_rejects(self):self.assertFalse(self.render(replace(self.new,image_data_uri=''))[2])
 def test_duplicate_peer_rejects(self):self.assertFalse(self.render(replace(self.new,physical_rows=self.new.physical_rows*2))[2])
 def test_changed_numeric_source_rejects(self):
  row=replace(self.new.physical_rows[0],cells=(*self.cells[:2],'-0.18\n0\n0.03',self.cells[3]),cell_words=(*self.words[:2],(*self.words[2][:2],('0.03',*self.words[2][2][1:])),self.words[3]))
  self.assertFalse(self.render(replace(self.new,physical_rows=(row,)))[2])
 def test_missing_word_rejects(self):
  row=replace(self.row,cell_words=(*self.words[:2],self.words[2][:-1],self.words[3]))
  self.assertFalse(self.render(replace(self.new,physical_rows=(row,)))[2])
 def test_changed_unit_rejects(self):
  row=replace(self.row,cells=(*self.cells[:3],'dB\ndB\ndB'),cell_words=(*self.words[:3],tuple(('dB',*w[1:]) for w in self.words[3])))
  self.assertFalse(self.render(replace(self.new,physical_rows=(row,)))[2])
 def test_capture_requires_all_original_guards(self):
  observed=[[dict(zip(('text','x0','top','x1','bottom'),word)) for word in cell] for cell in self.words]
  table=NS(rows=[NS(cells=self.boxes,bbox=(0,0,400,100))])
  for failing in [None,'_table_data_cell_geometry_is_complete','_table_row_bbox_matches_raw_cells','_source_cells_match_observed_geometry']:
   with patch('protocol_pdf_diff.pdf_extract._table_data_cell_geometry_is_complete',return_value=failing!='_table_data_cell_geometry_is_complete'),patch('protocol_pdf_diff.pdf_extract._table_row_bbox_matches_raw_cells',return_value=failing!='_table_row_bbox_matches_raw_cells'),patch('protocol_pdf_diff.pdf_extract._source_cells_match_observed_geometry',return_value=failing!='_source_cells_match_observed_geometry'):
    rows=p.capture_physical_rows(None,table,[list(self.cells)],[observed],[],1,1)
    self.assertEqual(1 if failing is None else 0,len(rows))
 def test_short_descriptor_shape_not_enabled(self):
  cells=('Equalizer\nStep size',*self.cells[1:])
  table=NS(rows=[NS(cells=self.boxes,bbox=(0,0,400,100))])
  with patch('protocol_pdf_diff.pdf_extract._table_data_cell_geometry_is_complete',return_value=True):
   self.assertFalse(p.capture_physical_rows(None,table,[cells],[[]],[],1,1))
 def test_csv_exact_roundtrip_and_missing_csv(self):
  _,payload,_=self.render()
  with TemporaryDirectory() as folder:
   path=Path(folder)/'physical.csv';self.assertEqual(2,len(p.write_physical_csv(payload,path)))
   with path.open(encoding='utf-8-sig',newline='') as stream: fields,*expected=list(csv.reader(stream))
   self.assertTrue(p.verify_physical_csv(path,fields,expected));path.unlink();self.assertFalse(p.verify_physical_csv(path,fields,expected))

if __name__=='__main__':unittest.main()
