import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import test_table_view_transaction as fixtures

from protocol_pdf_diff import table_view_transaction as t


def numeric_row(value,units='ns/mm',identity='delay'):
    f=fixtures.RowEvidenceTests();f.setUp();f.rows[1]=[identity,value,units]
    chars=[];f.words=[]
    for i,row in enumerate(f.rows):
        words=[]
        for j,text in enumerate(row):
            words.append([{'text':text,'x0':j*100+1,'x1':j*100+len(text)+2,'top':i*10+1,'bottom':i*10+8,'size':7}] if text else [])
            for k,c in enumerate(text):
                if not c.isspace():
                    n=len(chars);chars.append((n,c,j*100+1+k,i*10+1,j*100+2+k,i*10+8,n,1))
        f.words.append(words)
    f.page._physical_native_evidence=('text',tuple(chars))
    return f.captured()[0]

class NumericLiteralTests(unittest.TestCase):
    def test_complete_typed_literals_only(self):
        for a,b in [('6.141E‐03','6.141E-03'),('‐12.50','-12.50'),('‐.5e‐03','-.5e-03')]:
            self.assertTrue(t._same_three_cells(['gain',a,'V'],['gain',b,'V']))
    def test_value_sign_exponent_units_and_column_negative(self):
        old=['gain','6.141E‐03','V']
        for new in [['gain','6.141E-04','V'],['gain','6.141E+03','V'],['gain','6.142E-03','V'],['gain','6.141E-03','mV'],['gain','V','6.141E-03'],['other','6.141E-03','V']]:
            self.assertFalse(t._same_three_cells(old,new))
    def test_placeholders_expressions_pua_extra_text_refused(self):
        for value in ['‐‐','‐1/3','(V +V )/2 ↵ ‐1 1','6.141E‐03 800mV','6.141E ‐03','\uf02d1','1‐2']:
            self.assertIsNone(t._complete_numeric_literal_key(value))
    def test_source_raw_character_retained(self):
        row=numeric_row('6.141E‐03');self.assertIn('‐',[c[1] for c in row['native_chars']]);self.assertEqual(row['cells'][1],'6.141E‐03')

class NumericReportTests(fixtures.FinalReceiptTests):
    def setUp(self):
        super().setUp();self.install('6.141E‐03','6.141E-03')
    def install(self,old,new,new_units='ns/mm',old_units='ns/mm',identity='delay'):
        rows=[numeric_row(old,old_units,identity),numeric_row(new,new_units,identity)]
        tables=[]
        for i,row in enumerate(rows):
            table=getattr(self.bundle.original,['old_table_visuals','new_table_visuals'][i])[0]
            tables.append(replace(table,row_texts=['表格行: T1 | Parameter='+row['cells'][0]+' | Setting='+row['cells'][1]+' | Units='+row['cells'][2]],raw_source_cells=(tuple(row['cells']),),raw_cell_bounds=(tuple(row['cell_bboxes']),)))
        self.bundle.original=replace(self.bundle.original,old_table_visuals=[tables[0]],new_table_visuals=[tables[1]])
        self.bundle.candidate=copy.deepcopy(self.bundle.original)
        self.bundle.rows=[dict(rows[0],name='old.pdf',page=1),dict(rows[1],name='new.pdf',page=2,proposed=False)]
    def test_authentic_one_side_changes_fallback(self):
        for value,unit in [('6.141E-04','ns/mm'),('6.141E+03','ns/mm'),('6.142E-03','ns/mm'),('6.141E-03','ns')]:
            self.install('6.141E‐03',value,unit)
            with tempfile.TemporaryDirectory() as directory:
                out=t.write_reports_transaction(self.bundle,directory,self.options)
                self.assertIs(out.selected_result,self.bundle.original)
    def test_nonphysical_roles_real_writer_preserves_change(self):
        for identity,units in [('encoding_id',''),('firmware_version','code'),('serial_number','identifier')]:
            self.install('1E‐03','1E-03',units,units,identity)
            with tempfile.TemporaryDirectory() as directory:
                out=t.write_reports_transaction(self.bundle,directory,self.options)
                self.assertIs(out.selected_result,self.bundle.original)
                self.assertNotIn('three_cell_records',out.outputs['json'].read_text())
                text=out.outputs['json'].read_text();self.assertIn('1E‐03',text);self.assertIn('1E-03',text)
    def test_same_raw_ascii_nonphysical_still_accepted(self):
        self.install('1E-03','1E-03','code','code','firmware_version')
        with tempfile.TemporaryDirectory() as directory:
            out=t.write_reports_transaction(self.bundle,directory,self.options)
            self.assertIs(out.selected_result,self.bundle.candidate)
    def test_duplicate_identity_refuses(self):
        self.bundle.rows.append(copy.deepcopy(self.bundle.rows[0]))
        with tempfile.TemporaryDirectory() as directory:self.assertFalse(t._write_receipts(self.bundle,Path(directory)))
    def test_raw_hyphen_both_sides_all_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            out=t.write_reports_transaction(self.bundle,directory,self.options)
            self.assertIs(out.selected_result,self.bundle.candidate)
            for key in ['html','markdown','text','json']:
                text=out.outputs[key].read_text();self.assertIn('6.141E‐03',text);self.assertIn('6.141E-03',text)
            csv=(out.outputs['json'].parent/'three_cell_records.csv').read_text();self.assertIn('6.141E‐03',csv);self.assertIn('6.141E-03',csv)
if __name__=='__main__':unittest.main()
