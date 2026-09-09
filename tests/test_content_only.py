"""User contract: compare requirements, not publication furniture or wrapping."""
from pathlib import Path
import tempfile
import unittest

from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import DiffOptions, DiffResult, ExtractionResult, PageText, TableVisual
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.table_codec import encode_table_field


class ContentOnlyTests(unittest.TestCase):
    def report(self, old, new):
        options = DiffOptions(visual_watchdog=False)
        result = compare_extractions(
            ExtractionResult(Path('old.pdf'), [PageText(1, old)]),
            ExtractionResult(Path('new.pdf'), [PageText(1, new)]), options)
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(result, directory, options)
            return paths['markdown'].read_text(), paths['html'].read_text()

    def test_publication_authors_and_numbered_contents_are_not_changes(self):
        for title, old, new in [
            ('0.2 Authors', 'Alice\nEmail: alice@example.test', 'Bob\nEmail: bob@example.test'),
            ('0.4 Contents', '1 Scope ........ 5\n2 Limits ........ 6', '2 Scope ........ 8\n3 Limits ........ 9'),
        ]:
            md, html = self.report(f'{title}\n{old}\n1 Scope\nThe receiver shall support mode A.',
                                   f'{title}\n{new}\n1 Scope\nThe receiver shall support mode A.')
            self.assertNotIn('alice@example.test', md)
            self.assertNotIn('bob@example.test', md)
            self.assertNotIn('Limits ........', md)

    def test_ordinary_case_and_whitespace_do_not_create_reader_changes(self):
        for old, new in [('The MAXIMUM VALUE is specified.', 'The Maximum Value is specified.'),
                         ('The MAXIMUM value is specified.', 'The maximum value is specified.'),
                         ('The output  voltage is specified.', 'The output voltage is specified.')]:
            md, html = self.report('1 Requirements\n' + old, '1 Requirements\n' + new)
            self.assertNotIn('\n### ', md)

    def test_renumbered_and_reordered_complete_sections_do_not_change_content(self):
        a = 'The receiver shall support mode A and report the measured voltage.'
        b = 'The transmitter shall disable output when the supply is absent.'
        md, html = self.report(f'1 Receiver\n{a}\n2 Transmitter\n{b}',
                               f'3 Transmitter\n{b}\n4 Receiver\n{a}')
        self.assertNotIn('\n### ', md)

    def test_meaningful_changes_survive(self):
        for old, new in [('The voltage shall be 10 mV.', 'The voltage shall be 10 MV.'),
                         ('The receiver shall enable output.', 'The receiver shall not enable output.')]:
            md, _ = self.report('1 Requirements\n' + old, '1 Requirements\n' + new)
            self.assertIn(old, md)
            self.assertIn(new, md)

    def test_email_changes_are_excluded_in_body_too(self):
        md, _ = self.report('1 Requirements\nSend the fault report to a@example.test.',
                            '1 Requirements\nSend the fault report to b@example.test.')
        self.assertNotIn('\n### ', md)

    def test_email_exclusion_keeps_neighboring_requirement_changes(self):
        md, _ = self.report('1 Requirements\nNotify a@example.test within 5 seconds.',
                            '1 Requirements\nNotify b@example.test within 8 seconds.')
        self.assertIn('5 seconds', md)
        self.assertIn('8 seconds', md)
        self.assertNotIn('@example.test', md)

    def test_technical_identifiers_and_word_boundaries_remain_distinct(self):
        from protocol_pdf_diff.content_equivalence import cosmetic_content_equal
        for old, new in [('The state is IDLE.', 'The state is idle.'),
                         ('Set MODE_FAST now.', 'Set mode_fast now.'),
                         ('The voltage is 10 mV.', 'The voltage is 10 MV.'),
                         ('Use ab cd.', 'Use abcd.'),
                         ('Value=10 ↵ 20', 'Value=10 20')]:
            self.assertFalse(cosmetic_content_equal(old, new, cell_wrap=True))

    def table_report(self, old, new, *, reliable=True):
        def table(value):
            rows = ['表格行: T1 | Parameter=Limit | ' + encode_table_field('Description', value),
                    '表格行: T1 | Parameter=Other | Value=2']
            return TableVisual(1, 1, 'Table 1. Limits', (0, 0, 100, 100), '', rows, 'grid',
                               content_fully_represented=True, row_alignment_reliable=reliable)
        result = DiffResult(Path('old.pdf'), Path('new.pdf'), [], [], [], [],
                            old_table_visuals=[table(old)], new_table_visuals=[table(new)])
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(result, directory, DiffOptions())
            return paths['markdown'].read_text()

    def test_cell_wrapping_and_case_are_not_content_changes(self):
        for old, new in [('Output\nvoltage', 'Output voltage'), ('MAXIMUM VALUE', 'Maximum value')]:
            md = self.table_report(old, new)
            self.assertNotIn('配对相似度', md)

    def test_identical_uncertain_table_has_no_difference_card(self):
        md = self.table_report('Output voltage', 'Output voltage', reliable=False)
        self.assertNotIn('多行单元格归属未验证', md)
        self.assertNotIn('配对相似度', md)

    def test_uncertain_table_real_value_change_remains_visible(self):
        md = self.table_report('10 mV', '20 mV', reliable=False)
        self.assertIn('10 mV', md)
        self.assertIn('20 mV', md)
