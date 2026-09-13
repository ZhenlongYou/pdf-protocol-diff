import unittest
from protocol_pdf_diff import reporting as r, pdf_extract as e


def row(condition, value, extra=''):
    return '表格行: T1 | Characteristic= | Symbol= | Condition='+condition+' | MAX='+value+' | UNIT='+extra


class BoundedTableCandidateTests(unittest.TestCase):
    def setUp(self):
        self.old=[row(f,v) for f,v in [('10kHz','-93'),('100kHz','-113'),('1MHz','-133'),('10MHz','-143')]]
    def changes(self,new):
        return r._resolve_duplicate_primary_table_rows(self.old,new)[0]
    def test_one_soft_break_does_not_expand_to_four_records(self):
        new=self.old.copy();new[-1]=row('10\\nMHz','-143')
        self.assertEqual(2,len(self.changes(new)))
    def test_numeric_change_retained(self):
        new=self.old.copy();new[2]=row('1MHz','-134')
        self.assertEqual(2,len(self.changes(new)))
    def test_condition_change_retained(self):
        new=self.old.copy();new[0]=row('20kHz','-93')
        self.assertEqual(2,len(self.changes(new)))
    def test_reorder_retained(self):
        new=self.old.copy();new[:2]=reversed(new[:2])
        self.assertEqual(4,len(self.changes(new)))
    def test_insertion_does_not_cancel_by_projected_index(self):
        self.assertEqual(9,len(self.changes([row('5kHz','-83')]+self.old)))
    def test_extra_cell_retained(self):
        new=self.old.copy();new[0]+=' | Note=Note B'
        self.assertEqual(2,len(self.changes(new)))
    def test_unit_change_retained(self):
        new=self.old.copy();new[0]=row('10kHz','-93','dBc/Hz')
        self.assertEqual(2,len(self.changes(new)))
    def test_changed_anchor_does_not_borrow_identityless_positions(self):
        old=self.old.copy();new=self.old.copy()
        old.insert(1,'表格行: T1 | Parameter=Alpha | Value=1')
        new.insert(1,'表格行: T1 | Parameter=Beta | Value=1')
        new[-1]=row('10\\nMHz','-143')
        changes,_,_=r._resolve_duplicate_primary_table_rows(old,new)
        self.assertEqual(8,len(changes))
    def test_identical_record_moved_between_repeated_anchors_is_visible(self):
        anchor='表格行: T1 | Parameter=Repeated anchor | Value=1'
        a,b=row('10kHz','-93'),row('100kHz','-113')
        old=[anchor,a,anchor,b]
        new=[anchor,b,anchor,a]
        changes,_,_=r._resolve_duplicate_primary_table_rows(old,new)
        rendered=' '.join(c.old_value+' '+c.new_value for c in changes)
        self.assertGreaterEqual(len(changes),4)
        self.assertIn('-93',rendered)
        self.assertIn('-113',rendered)
    def test_min_max_header_interchange_keeps_value_change(self):
        from protocol_pdf_diff.models import TableVisual
        def table(headers):
            return TableVisual(page_number=1,table_number=1,title='Table 1',
                bbox=(0,0,100,100),image_data_uri='',grid_summary='',
                row_texts=[e._format_table_row(['Limit','1','2'],headers,1)],
                row_alignment_reliable=True)
        changes=r._table_row_changes(
            (table(['Parameter','Min Value','Max Value']),),
            (table(['Parameter','Max Value','Min Value']),))
        self.assertTrue(changes)
        rendered=' '.join(c.old_value+' '+c.new_value for c in changes)
        self.assertIn('Min Value=1',rendered)
        self.assertIn('Min Value=2',rendered)
    def test_compound_header(self):
        for h in [('Parameter','Min Value','Max Value'),('Tap Position','Min Value','Max Value')]:
            self.assertTrue(e._looks_like_table_header(list(h)))
    def test_numeric_record_not_header(self):
        for h in [('Minimum voltage','5','V'),('Parameter','0.1','0.3'),('Tap Position','Min Value','0.4')]:
            self.assertFalse(e._looks_like_table_header(list(h)))
    def test_header_labels_preserved(self):
        text=e._format_table_row(['x','1','2'],['Parameter','Min Value','Max Value'],1)
        self.assertIn('Min Value=1',text);self.assertIn('Max Value=2',text)

if __name__=='__main__': unittest.main()
