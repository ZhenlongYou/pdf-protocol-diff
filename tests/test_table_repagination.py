"""真实两列表格的跨页反例：同文、数值、否定与同名独立行。"""
import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace

import fitz
from protocol_pdf_diff.models import DiffOptions
from protocol_pdf_diff.table_view_transaction import run_diff_transaction, report_outcome
from protocol_pdf_diff.compare import run_diff
from protocol_pdf_diff.reporting import _table_row_changes, _table_geometry_supports_page_boundary_continuation
from protocol_pdf_diff.table_repagination import review_split_cells

HEAD = 'The receiver shall retain lock during reset'
TAIL = 'and resume operation after the interval ends.'


def write_split_table_pair(root, *, head=HEAD, old_tail=TAIL, new_tail=TAIL, other_key='Voltage'):
    """唯一已知变化是单元格的物理拆页，以及调用方明确注入的文字修改。"""
    def draw_table(page, y, rows):
        xs=(55,200,560);height=70
        page.insert_text((55,y-12),'Table 1. Operating Limits',fontsize=12)
        rows=[['Parameter','Requirement'],*rows]
        for i,row in enumerate(rows):
            for j,value in enumerate(row):
                rect=(xs[j]+5,y+i*height+8,xs[j+1]-5,y+(i+1)*height-5)
                assert page.insert_textbox(rect,value,fontsize=11)>=0
        for x in xs:
            page.draw_line((x,y),(x,y+len(rows)*height),width=.8)
        for i in range(len(rows)+1):
            page.draw_line((55,y+i*height),(560,y+i*height),width=.8)
    old,new=root/'old.pdf',root/'new.pdf'
    with fitz.open() as pdf:
        page=pdf.new_page(width=612,height=792)
        page.insert_text((55,50),'1 Operating Requirements',fontsize=16)
        draw_table(page,520,[['Recovery',head+' '+old_tail],[other_key,'3.3 V']])
        pdf.save(old)
    with fitz.open() as pdf:
        page=pdf.new_page(width=612,height=792)
        page.insert_text((55,50),'1 Operating Requirements',fontsize=16)
        draw_table(page,600,[['Recovery',head]])
        page=pdf.new_page(width=612,height=792)
        draw_table(page,80,[['Recovery',new_tail],[other_key,'3.3 V']])
        pdf.save(new)
    return old,new


class TableRepaginationTests(unittest.TestCase):
    def test_missing_geometry_changed_columns_or_other_rows_keep_original_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            old,new=write_split_table_pair(Path(tmp))
            result=run_diff(old,new,DiffOptions())
            left,right=tuple(result.old_table_visuals),tuple(result.new_table_visuals)
            original=_table_row_changes(left,right)
            self.assertEqual(1,len(review_split_cells(original,left,right,_table_geometry_supports_page_boundary_continuation)))
            first,last=right
            modified_cells=list(last.raw_source_cells)
            modified_cells[-1]=('Voltage','5.5 V')
            shifted_columns=tuple(((a[0],a[1],a[2]+1,a[3]),(b[0]+1,b[1],b[2],b[3]))
                                  for a,b in last.raw_cell_bounds)
            cases=(
                (replace(first,raw_cell_bounds=()),last),
                (first,replace(last,page_number=last.page_number+1)),
                (first,replace(last,raw_source_cells=tuple(modified_cells))),
                (first,replace(last,context_words=last.context_words[:-1])),
                (first,replace(last,raw_cell_bounds=shifted_columns)),
                (first,replace(last,raw_cell_bounds=tuple((None,*row[1:]) for row in last.raw_cell_bounds))),
                (first,replace(last,row_texts=list(reversed(last.row_texts)))),
                (first,last,last),
            )
            for candidate in cases:
                with self.subTest(candidate=len(candidate)):
                    self.assertIs(original,review_split_cells(original,left,candidate,_table_geometry_supports_page_boundary_continuation))

    def test_split_cell_is_one_sourced_review_not_six_confirmed_changes(self):
        for old_tail,new_tail in (
            (TAIL,TAIL),
            ('and resume operation after 10 ms when the interval ends.',
             'and resume operation after 20 ms when the interval ends.'),
            ('and shall not resume operation until the interval ends.',
             'and shall resume operation until the interval ends.'),
        ):
            with self.subTest(new_tail=new_tail), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                old,new=write_split_table_pair(root,old_tail=old_tail,new_tail=new_tail)
                options=DiffOptions()
                outcome=report_outcome(run_diff_transaction(old,new,options),root/'reports',options)
                data=json.loads(outcome.outputs['json'].read_text())
                tables=data['content_table_changes']
                self.assertEqual(1,len(tables))
                change=tables[0]
                self.assertEqual('review',change['change_type'])
                self.assertEqual(1,len(change['row_changes']))
                row=change['row_changes'][0]
                self.assertIn('跨页',row['item'])
                self.assertEqual('需人工复核',row['change_type'])
                self.assertIn(old_tail,' '.join(row['old_value'].split()))
                self.assertIn(new_tail,' '.join(row['new_value'].split()))
                self.assertIn('Requirement',row['new_value'])
                self.assertEqual({('old',1),('new',1),('new',2)},
                                 {(r['side'],r['page']) for r in row['source_receipts']})

    def test_two_pages_joined_back_into_one_stays_a_sourced_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            single,split=write_split_table_pair(root)
            options=DiffOptions()
            outcome=report_outcome(run_diff_transaction(split,single,options),root/'reports',options)
            changes=json.loads(outcome.outputs['json'].read_text())['content_table_changes']
            self.assertEqual(1,len(changes))
            self.assertEqual('review',changes[0]['change_type'])
            self.assertEqual(1,len(changes[0]['row_changes']))
            self.assertEqual({('old',1),('old',2),('new',1)},
                             {(r['side'],r['page']) for r in changes[0]['row_changes'][0]['source_receipts']})

    def test_two_complete_same_named_rows_are_not_merged(self):
        for overrides in (dict(head=HEAD+'.',
                old_tail='The receiver shall resume operation after the interval ends.',
                new_tail='The receiver shall resume operation after the interval ends.'),
                dict(other_key='Recovery')):
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                old,new=write_split_table_pair(root,**overrides)
                outcome=report_outcome(run_diff_transaction(old,new,DiffOptions()),root/'reports',DiffOptions())
                data=json.loads(outcome.outputs['json'].read_text())
                self.assertTrue(data['content_table_changes'])
                self.assertFalse(any('跨页单元格' in r['item'] for t in data['content_table_changes'] for r in t['row_changes']))
