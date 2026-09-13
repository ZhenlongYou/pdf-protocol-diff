import copy
import json
from pathlib import Path
import unittest
from test_merged_cell_candidate import fixture
from protocol_pdf_diff import pdf_extract as e


def unit_fixture():
    rows,bounds,words=fixture()
    rows[0][2]='UNIT'; words[0][2][0]['text']='UNIT'
    rows[1][2]='dBc/Hz'; words[1][2][0]['text']='dBc/Hz'
    return rows,bounds,words


class UnitOwnerTests(unittest.TestCase):
    def test_six_original_tables_keep_conditions_numbers_and_blank_numeric_cells(self):
        from protocol_pdf_diff.compare import _parsed_table_row_fields
        data=json.loads((Path(__file__).parent/'fixtures/merged-unit-source.json').read_text())
        for item in data:
            r,b,w=item['rows'],item['bounds'],item['words']
            lines=e._table_lines_from_rows_with_data_evidence(r,1,cell_word_rows=w,cell_bounds_rows=b)[0]
            self.assertEqual(5,len(lines))
            for source,line in zip(r[1:],lines):
                fields=dict(_parsed_table_row_fields(line))
                self.assertEqual('dBc/Hz',fields['UNIT'])
                for ci,label in enumerate(r[0]):
                    if source[ci] is not None:
                        self.assertEqual(''.join(source[ci].split()),''.join(fields[label.rstrip('.')].split()))

    def test_explicit_unit_header_with_exact_source_owner(self):
        for header in ['Unit','UNIT','Units']:
            r,b,w=unit_fixture();r[0][2]=header;w[0][2][0]['text']=header
            self.assertEqual({(2,2):1},e._merged_cell_display_owners(r,b,w))

    def test_blank_independent_cell_or_empty_string_never_inherits(self):
        for kind in ['empty_string','physical_cell']:
            r,b,w=unit_fixture()
            if kind=='empty_string':r[2][2]=''
            else:b[2][2]=(200,20,300,30)
            self.assertEqual({},e._merged_cell_display_owners(r,b,w))

    def test_missing_bounds_words_or_changed_source_text_rejects(self):
        for kind in ['bounds','words','different_unit']:
            r,b,w=unit_fixture()
            if kind=='bounds':b=None
            elif kind=='words':w[1][2]=[]
            else:r[1][2]='dB/Hz'
            self.assertEqual({},e._merged_cell_display_owners(r,b,w))

    def test_wrong_header_column_or_cross_column_box_rejects(self):
        for kind in ['header','ownerbox']:
            r,b,w=unit_fixture()
            if kind=='header':r[0][2],r[0][3]=r[0][3],r[0][2]
            else:b[1][2]=(100,10,200,30)
            self.assertEqual({},e._merged_cell_display_owners(r,b,w))

    def test_min_max_are_still_not_inherited(self):
        for label in ['MIN','MAX','Minimum','Maximum']:
            r,b,w=unit_fixture();r[0][2]=label;w[0][2][0]['text']=label
            self.assertEqual({},e._merged_cell_display_owners(r,b,w))

    def test_unit_header_cannot_borrow_another_column_word(self):
        r,b,w=unit_fixture();w[0][2][0]['text']='MAX'
        self.assertEqual({},e._merged_cell_display_owners(r,b,w))
        r,b,w=unit_fixture();w[0][2][0]['x0']=101.;w[0][2][0]['x1']=151.
        self.assertEqual({},e._merged_cell_display_owners(r,b,w))

    def test_coherent_unit_change_and_numbers_remain_literal(self):
        r,b,w=unit_fixture();original=copy.deepcopy((r,b,w))
        a=e._table_lines_from_rows_with_data_evidence(r,1,cell_word_rows=w,cell_bounds_rows=b)[0]
        self.assertEqual(original,(r,b,w))
        self.assertIn('UNIT=dBc/Hz',a[-1]);self.assertIn('MAX=2',a[-1])
        r[1][2]='dB/Hz';w[1][2][0]['text']='dB/Hz';r[2][3]='3';w[2][3][0]['text']='3'
        z=e._table_lines_from_rows_with_data_evidence(r,1,cell_word_rows=w,cell_bounds_rows=b)[0]
        self.assertIn('UNIT=dB/Hz',z[-1]);self.assertIn('MAX=3',z[-1]);self.assertNotEqual(a,z)


if __name__=='__main__':unittest.main()
