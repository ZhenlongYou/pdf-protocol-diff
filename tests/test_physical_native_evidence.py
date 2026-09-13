import unittest,io,base64
from dataclasses import replace
from types import SimpleNamespace as NS
from PIL import Image
from protocol_pdf_diff.models import PhysicalTableRow,TableVisual,SnippetPair
from protocol_pdf_diff import physical_native_evidence as n
from protocol_pdf_diff.physical_table_rows import render_physical_appendix,authorized_spans


def source(side,changed=False,second_value='0.02'):
 image=io.BytesIO();Image.new('RGB',(8,8)).save(image,format='PNG');uri='data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()
 text=[];rows=[];counter=0
 for ri in range(2):
  val=second_value if ri==1 else '0.03' if changed else '0.02'
  cells=(f'Limit {ri}\nMin\nMax\nStep size',f'c{ri}',f'-1\n0\n{val}','dB\ndB\ndB')
  boxes=tuple((ci*100,ri*50,(ci+1)*100,ri*50+45) for ci in range(4));charcells=[[] for _ in range(4)];wordcells=[[] for _ in range(4)]
  for line in range(4):
   tokens=[]
   for ci,cell in enumerate(cells):
    values=cell.splitlines();li=line if ci==0 else (0 if line==0 else -1) if ci==1 else line-1
    if li<0 or li>=len(values):continue
    value=values[li];x=ci*100+2;y=ri*50+line*10+2
    tokens.append(value)
    wordcells[ci].append((value,x,y,x+len(value)*2,y+5))
    for j,c in enumerate(value):
     if not c.isspace():
      charcells[ci].append((counter,c,x+j*2,y,x+j*2+1,y+5,counter,1));counter+=1
   text.append(' '.join(tokens))
  chars=tuple(sorted((c for col in charcells for c in col),key=lambda c:c[0]))
  rows.append(PhysicalTableRow(side+str(ri),cells,(0,ri*50,400,ri*50+45),boxes,tuple(tuple(c) for c in wordcells),chars))
 table=TableVisual(1,1,'Table 1',(0,0,400,100),uri,[],'',physical_rows=tuple(rows))
 page=NS(page_number=1,ocr_used=False,physical_native_text=' '.join(text))
 return table,NS(pages=[page])

class NativeOwnershipTests(unittest.TestCase):
 def setUp(self):
  self.old,self.ox=source('old');self.new,self.nx=source('new')
  self.sections=[NS(start_page=1,end_page=1,section_id=s) for s in ['oldsec','newsec']]
  self.change=NS(old_section=self.sections[0],new_section=self.sections[1],audit_replaced_snippets=None,replaced_snippets=[SnippetPair('Step size 0.02 dB','Step size')])
 def candidates(self):
  return n.native_owned_candidates(NS(changes=[self.change],old_table_visuals=[self.old],new_table_visuals=[self.new]),self.ox,self.nx)
 def receipts(self):
  return render_physical_appendix([NS(old_tables=(self.old,),new_tables=(self.new,))])[2]
 def test_all_repeat_occurrences_have_conjunctive_receipts(self):
  c=self.candidates();self.assertTrue(c)
  self.assertTrue(authorized_spans(c,'old','oldsec',self.receipts()))
  self.assertTrue(authorized_spans(c,'new','newsec',self.receipts()))
 def test_missing_any_row_receipt_blocks_both_sides(self):
  c=self.candidates();receipts=self.receipts()
  for receipt in receipts:
   subset=receipts-{receipt}
   self.assertFalse(authorized_spans(c,'old','oldsec',subset));self.assertFalse(authorized_spans(c,'new','newsec',subset))
 def test_shared_short_new_text_requires_other_old_counterpart(self):
  self.old,self.ox=source('old',second_value='0.04');self.new,self.nx=source('new',second_value='0.04')
  candidates=self.candidates();self.assertTrue(candidates)
  receipts=self.receipts()-{('old','old1')}
  self.assertFalse(authorized_spans(candidates,'new','newsec',receipts))
  self.assertFalse(authorized_spans(candidates,'old','oldsec',receipts))
 def test_one_side_changed_source_cell_blocks_all(self):
  self.new,self.nx=source('new',changed=True);self.assertFalse(self.candidates())
 def test_extra_same_text_outside_table_blocks(self):
  self.nx.pages[0].physical_native_text+=' Step size'
  self.assertFalse(self.candidates())
 def test_missing_original_character_blocks(self):
  row=self.old.physical_rows[0];row=replace(row,native_chars=row.native_chars[:-1]);self.old=replace(self.old,physical_rows=(row,self.old.physical_rows[1]));self.assertFalse(self.candidates())
 def test_shared_glyph_blocks(self):
  row=self.old.physical_rows[0];chars=list(row.native_chars);chars[0]=(*chars[0][:7],2);row=replace(row,native_chars=tuple(chars));self.old=replace(self.old,physical_rows=(row,self.old.physical_rows[1]));self.assertFalse(self.candidates())
 def test_pua_original_blocks(self):
  row=self.old.physical_rows[0];chars=list(row.native_chars);chars[0]=(chars[0][0],'\ue001',*chars[0][2:]);self.assertFalse(n.valid_native_row(replace(row,native_chars=tuple(chars))))
 def test_cross_row_phrase_rejected(self):
  self.assertIsNone(n._occurrence_owners('0.02 dB Limit 1',self.sections[0],self.ox.pages,[(1,r) for r in self.old.physical_rows]))
 def test_missing_page_native_evidence_blocks(self):
  self.nx.pages[0].physical_native_text=None;self.assertFalse(self.candidates())
 def test_missing_image_blocks_authorization(self):
  self.new=replace(self.new,image_data_uri='');self.assertTrue(self.candidates());self.assertFalse(authorized_spans(self.candidates(),'old','oldsec',self.receipts()))
 def test_changed_cell_order_cannot_reuse_native_receipt(self):
  row=self.old.physical_rows[0];self.assertFalse(n.valid_native_row(replace(row,cells=(*row.cells[:2],'0\n-1\n0.02',row.cells[3]))))
 def test_filtered_offsets_rebind_to_original_page_stream(self):
  prefix='Printed page 123 '
  offset=len(''.join(prefix.split()))
  original=tuple((c[0]+offset,*c[1:6],c[6]+offset,c[7]) for row in self.old.physical_rows for c in row.native_chars)
  page=NS(_physical_native_evidence=(prefix+self.ox.pages[0].physical_native_text,original))
  self.ox.pages[0].physical_native_text=page._physical_native_evidence[0]
  self.assertFalse(self.candidates())
  rebound=n.rebind_native_table_rows([self.old],page)[0]
  self.assertEqual(self.old.physical_rows[0].cells,rebound.physical_rows[0].cells)
  self.old=rebound
  self.assertTrue(self.candidates())
 def test_failed_rebinding_discards_stale_authority_keeps_cells(self):
  rebound=n.rebind_native_table_rows([self.old],NS(_physical_native_evidence=(None,())))[0]
  self.assertEqual([r.cells for r in self.old.physical_rows],[r.cells for r in rebound.physical_rows])
  self.assertTrue(all(not r.native_chars for r in rebound.physical_rows))
  self.old=rebound
  self.assertFalse(self.candidates())
 def test_rebinding_changed_native_value_cannot_keep_old_proof(self):
  chars=[c for row in self.old.physical_rows for c in row.native_chars]
  index=next(i for i,c in enumerate(chars) if c[1]=='2')
  chars[index]=(chars[index][0],'3',*chars[index][2:])
  rebound=n.rebind_native_table_rows([self.old],NS(_physical_native_evidence=('changed',tuple(chars))))[0]
  self.assertFalse(rebound.physical_rows[0].native_chars)

if __name__=='__main__':unittest.main()
