"""通用用途经过公开比较和全部报告出口；默认协议行为仍由旧测试约束。"""
import json
import tempfile
import unittest
from pathlib import Path
from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.comparison_policy import comparison_profile
from protocol_pdf_diff.content_equivalence import cosmetic_content_equal


class ComparisonProfileTests(unittest.TestCase):
    def report(self, old, new, profile='general'):
        options = DiffOptions(comparison_profile=profile, visual_watchdog=False)
        result = compare_extractions(ExtractionResult(Path('old.pdf'), [old]),
                                    ExtractionResult(Path('new.pdf'), [new]), options)
        with tempfile.TemporaryDirectory() as d:
            paths = write_reports(result, d, options)
            return result, {k: p.read_text(encoding='utf-8-sig') for k,p in paths.items() if p.suffix in ('.md','.html','.txt','.json','.csv')}

    def test_authors_and_email_survive_all_reader_formats(self):
        old=PageText(1,'0.2 Authors\nAlice\nEmail: alice@example.test\n1 Scope\nThe device shall remain enabled.')
        new=PageText(1,'0.2 Authors\nBob\nEmail: bob@example.test\n1 Scope\nThe device shall remain enabled.')
        _,outputs=self.report(old,new)
        for name in ('markdown','html','text','csv'):
            with self.subTest(format=name):
                self.assertIn('alice@example.test', outputs[name])
                self.assertIn('bob@example.test', outputs[name])
        payload=json.loads(outputs['json'])
        self.assertTrue(payload['content_changes'])
        self.assertEqual('general',payload['comparison_profile'])
        _,protocol=self.report(old,new,'protocol')
        self.assertFalse(json.loads(protocol['json'])['content_changes'])

    def test_publication_header_and_footer_remain_separate_changes(self):
        old=PageText(1,'1 Scope\nThe device shall remain enabled.',publication_header_texts=('User Manual Version 1.0',), running_footer_values=('Copyright 2025 Example',))
        new=PageText(1,old.text,publication_header_texts=('User Manual Version 2.0',), running_footer_values=('Copyright 2026 Example',))
        _,outputs=self.report(old,new)
        for text in ('1.0','2.0','2025','2026'):
            self.assertIn(text,outputs['markdown'])
        self.assertEqual(2,len(json.loads(outputs['json'])['content_changes']))

    def test_body_email_and_table_equivalence_follow_policy_and_reset(self):
        old='Notify a@example.test within 5 seconds.'
        new='Notify b@example.test within 5 seconds.'
        _,outputs=self.report(PageText(1,'1 Contact\n'+old),PageText(1,'1 Contact\n'+new))
        self.assertTrue(json.loads(outputs['json'])['content_changes'])
        with comparison_profile('general'):
            self.assertFalse(cosmetic_content_equal(old,new,cell_wrap=True))
        self.assertTrue(cosmetic_content_equal(old,new,cell_wrap=True))

    def test_invalid_profile_fails_before_comparison(self):
        with self.assertRaises(ValueError):
            DiffOptions(comparison_profile='unknown')
