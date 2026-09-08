"""Real PDF/report coverage gaps remain visible and tied to physical pages."""
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import fitz
from protocol_pdf_diff.compare import run_diff
from protocol_pdf_diff.models import DiffOptions
from protocol_pdf_diff.reporting import write_reports


def pdf(path, texts, offset=0):
    with fitz.open() as doc:
        for text in texts:
            p=doc.new_page()
            if text:
                p.insert_text((72+offset,72),text,fontsize=12)
            p.draw_rect(fitz.Rect(80,180,110,210),color=(0,0,1))
        doc.save(path)
    return path


class VisualCoverageDisclosureTests(unittest.TestCase):
    def check_reports(self, result, root):
        reports=write_reports(result, root/'reports', DiffOptions())
        html=reports['html'].read_text(encoding='utf-8')
        data=json.loads(reports['json'].read_text(encoding='utf-8'))
        issues=data['provenance']['visual_watchdog_run']['coverage_issues']
        audit=result.provenance.visual_watchdog_audit
        self.assertEqual(audit.ambiguous_page_count+audit.failed_page_pair_count,len(issues))
        self.assertIn('查看未核对页面及原因',html)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        for issue in issues:
            old=issue['old_page_number'] or '未确定';new=issue['new_page_number'] or '未确定'
            self.assertRegex(html, rf'<td>(?:<a[^>]*>)?{old}(?:</a>)?</td><td>(?:<a[^>]*>)?{new}(?:</a>)?</td>')
            self.assertIn(issue['reason'],html)
        return issues

    def test_unequal_blank_and_repeated_pages_are_not_claimed_compared(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            text='1 Requirements\nThe receiver shall preserve calibrated timing.'
            old=pdf(root/'old.pdf',['',text])
            new=pdf(root/'new.pdf',['','',text])
            result=run_diff(old,new,DiffOptions())
            issues=self.check_reports(result,root)
            self.assertIn((1,None),[(i['old_page_number'],i['new_page_number']) for i in issues])
            self.assertIn((None,1),[(i['old_page_number'],i['new_page_number']) for i in issues])
            self.assertIn((None,2),[(i['old_page_number'],i['new_page_number']) for i in issues])
            self.assertTrue(all('唯一' in i['reason'] for i in issues))

    def test_reflow_and_render_failure_have_distinct_locatable_causes(self):
        for cause in ('reflow','render'):
            with self.subTest(cause=cause), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);text='1 Requirements\nThe receiver shall preserve calibrated timing.'
                old=pdf(root/'old.pdf',[text])
                new=pdf(root/'new.pdf',[text],offset=20 if cause=='reflow' else 0)
                if cause=='render':
                    with patch('protocol_pdf_diff.visual_watchdog._render_page',side_effect=RuntimeError('test render failure')):
                        result=run_diff(old,new,DiffOptions())
                else:
                    result=run_diff(old,new,DiffOptions())
                issues=self.check_reports(result,root)
                self.assertEqual(1,len(issues))
                self.assertEqual((1,1),(issues[0]['old_page_number'],issues[0]['new_page_number']))
                self.assertIn('重排' if cause=='reflow' else 'RuntimeError',issues[0]['reason'])
