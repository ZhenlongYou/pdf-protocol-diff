import copy,json,unittest
from pathlib import Path
from dataclasses import replace
from protocol_pdf_diff.models import TableVisual
from protocol_pdf_diff.table_annotations import annotation_receipts,classify_annotations
from protocol_pdf_diff.reporting import _make_table_row_change,_table_row_changes
FIXTURE=json.loads((Path(__file__).parent/'fixtures/t49.json').read_text())
def table():
    d=copy.deepcopy(FIXTURE)
    return TableVisual(671,1,'Table 29-8',(157,155,463,458),'source-image',d['row_texts'],'grid',
        content_fully_represented=True,data_rows_fully_represented=True,row_alignment_reliable=True,
        raw_source_cells=d['raw_rows'],raw_cell_bounds=d['raw_bounds'],context_words=tuple(
            (w['text'],w['x0'],w['top'],w['x1'],w['bottom']) for note in d['notes'] for cell in note['words'] for w in cell))
def result(old,new):return classify_annotations(_table_row_changes(tuple(old),tuple(new)),old,new,_make_table_row_change)
class TableAnnotationRoleTests(unittest.TestCase):
    def test_added_table_has_thirteen_data_two_full_notes(self):
        rows=result([], [table()]);self.assertEqual(13,sum(r.row_role=='data' for r in rows));self.assertEqual(2,sum(r.row_role=='annotation' for r in rows));self.assertIn('4 dB',rows[-1].new_value);self.assertIn('should',rows[-1].new_value)
    def test_deleted_table_keeps_both_notes(self):
        rows=result([table()],[]);notes=[r for r in rows if r.row_role=='annotation'];self.assertEqual(2,len(notes));self.assertTrue(all(r.change_type=='表说明删除' for r in notes))
    def test_four_to_five_db_remains_modified(self):
        old=table();new=copy.deepcopy(old);new.raw_source_cells[-1][0]=new.raw_source_cells[-1][0].replace('4 dB','5 dB');new.row_texts[-1]=new.row_texts[-1].replace('4 dB','5 dB')
        new=replace(new,context_words=tuple(('5' if w[0]=='4' else w[0],*w[1:]) for w in new.context_words))
        rows=result([old],[new]);self.assertEqual(1,len(rows));self.assertEqual('annotation',rows[0].row_role);self.assertIn('4 dB',rows[0].old_value);self.assertIn('5 dB',rows[0].new_value)
    def test_note_parameter_with_other_value_not_classified(self):
        t=table();t.raw_source_cells[-1][1]='5';self.assertFalse(annotation_receipts(t))
    def test_half_width_note_not_classified(self):
        t=table();t.raw_cell_bounds[-1][0][2]=235.0104;self.assertFalse(annotation_receipts(t))
    def test_missing_note_word_not_classified(self):
        t=table();t=replace(t,context_words=t.context_words[:-1]);self.assertFalse(annotation_receipts(t))
    def test_missing_image_not_classified(self):self.assertFalse(annotation_receipts(replace(table(),image_data_uri='')))
    def test_missing_quality_not_classified(self):self.assertFalse(annotation_receipts(replace(table(),data_rows_fully_represented=False)))
    def test_nonfinite_geometry_not_classified(self):
        t=table();t.raw_cell_bounds[-1][0][3]=float('inf');self.assertFalse(annotation_receipts(t))
    def test_uncovered_extra_field_retained_as_original(self):
        for extra in [' | Voltage 800 mV',' | Voltage=800 mV',' | Unknown=',' suffix 800 mV']:
            with self.subTest(extra=extra):
                t=table();t.row_texts[-1]+=extra
                baseline=_table_row_changes((),(t,));rows=result([],[t]);self.assertIn(baseline[-1],rows)
                if '800' in extra:self.assertTrue(any('800' in r.new_value for r in rows))
                self.assertFalse(any(r.row_role=='annotation' and r.item=='Note 2' for r in rows))
    def test_changed_header_label_retained(self):
        t=table();t.row_texts[-1]=t.row_texts[-1].replace(' | gDC=', ' | Voltage 800 mV=')
        baseline=_table_row_changes((),(t,));rows=result([],[t]);self.assertIn(baseline[-1],rows)
        self.assertFalse(any(r.row_role=='annotation' and r.item=='Note 2' for r in rows))
    def test_changed_note_numeric_value_with_stale_receipt_retained(self):
        t=table();t.row_texts[-1]=t.row_texts[-1].replace('4 dB','5 dB')
        rows=result([],[t]);self.assertTrue(any('5 dB' in r.new_value for r in rows))
        self.assertFalse(any(r.row_role=='annotation' and r.item=='Note 2' for r in rows))
    def test_unchanged_notes_do_not_create_changes(self):self.assertEqual([],result([table()],[table()]))
if __name__=='__main__':unittest.main()
