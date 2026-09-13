"""Source-page counterexamples: correspondence and lossless reader semantics.

Expected values come from the independently audited PDF pages, not a second
invocation of the matcher. The serialized fixture preserves the escaped input.
"""
import dataclasses
import json
from pathlib import Path
import unittest

from protocol_pdf_diff import compare, reporting, sectioning
from protocol_pdf_diff.figure_filters import strip_coordinate_owned_visual_fragment
from protocol_pdf_diff.models import Section, TableVisual, DiffOptions
from protocol_pdf_diff.source_regions import running_footer_folio

FIXTURE = json.loads((Path(__file__).parent / "fixtures/content_correspondence.json").read_text())


def section(side, number):
    row = next(s for s in FIXTURE['sections'][side] if s['heading'].split()[0] == number)
    page = int(row['page_range'])
    return Section(row['section_id'], row['heading'], row['title'], row['level'],
                   tuple(row['location'].split(' / ')), tuple(row['number_path']),
                   page, page, row['body_preview'])


def tables(side, page):
    fields = {f.name for f in dataclasses.fields(TableVisual)}
    return [TableVisual(**{**{k: (tuple(v) if k in {'row_texts', 'bbox'} else v)
                                   for k, v in row.items() if k in fields}, 'image_data_uri': ''})
            for row in FIXTURE['tables'][side] if row['page_number'] == page]


class ContentCorrespondenceTests(unittest.TestCase):
    def test_running_folio_in_publisher_footer(self):
        for page in FIXTURE['footers']:
            words = page['bottom_rows'][0]['words']
            source = [(w['text'], w['x0'], w['top'], w['x1'], w['bottom']) for w in words]
            with self.subTest(page=page['page']):
                self.assertIsNotNone(running_footer_folio(source, (0, 0, *page['size'])))

    def test_formula_must_not_hide_definitions_or_note(self):
        for case, expected in zip(FIXTURE['formulas'], ('Where Q is 3.463', 'where Q5d = 4.2649')):
            text = ' '.join(compare._paragraph_review_units(case['unit'], suppressed_table_unit_keys=set()))
            self.assertIn(expected, text)
        text = ' '.join(compare._paragraph_review_units(FIXTURE['formulas'][1]['unit'], suppressed_table_unit_keys=set()))
        self.assertIn('NOTE 1', text)

    def test_character_inventory_cannot_erase_unrelated_prose(self):
        for case in FIXTURE['figure_cases'][:2]:
            cleaned = strip_coordinate_owned_visual_fragment(case['original'], (FIXTURE['figure_sources'][0]['text'],))
            start = 'The second recommended' if '350 mm' in case['original'] else 'The third recommended'
            self.assertIn(case['original'][case['original'].index(start):], cleaned)

    def test_removing_exact_label_keeps_following_negative_sign(self):
        text = 'Label Alpha Beta – 10.75 dB'
        self.assertIn('– 10.75', strip_coordinate_owned_visual_fragment(text, ('Label Alpha Beta',)))

    def test_formula_decimal_is_not_a_section_heading(self):
        self.assertTrue(sectioning._looks_like_formula_or_table_value_heading(
            '0.998', '+ 2.397 ------------- + 0.486 ------------- \uf02c f \uf0a3 f \uf0a3 f \uf0a4 2'))

    def test_damaged_parent_path_does_not_steal_existing_chapter(self):
        old = [section('old', '27.3')]
        new = [section('new', '27.3'), section('new', '28.4')]
        matched = compare._match_sections(old, new, DiffOptions())
        self.assertEqual((0, 0), matched[0][:2])

    def test_identical_unreliable_table_has_two_sided_correspondence(self):
        groups = reporting._paired_table_visuals(tables('old', 586), tables('new', 590))
        self.assertEqual([(1, 1)], [(len(g.old_tables), len(g.new_tables)) for g in groups])
        self.assertTrue(reporting._table_group_has_unreliable_multirow_alignment(groups[0]))

    def test_limit_labels_are_retained(self):
        value = reporting._table_row_value_display('Parameter=Swing | Min= | Max=1000 | Unit=mVppd')
        self.assertIn('Max=1000', value)

    def test_single_sided_schema_unit_survives(self):
        changes = reporting._table_row_changes((), tuple(tables('new', 655)))
        self.assertTrue(any('UI' in row.new_value for row in changes))


class CorrespondenceSafetyTests(unittest.TestCase):
    def test_formula_prefix_keeps_normative_change(self):
        for modal in ('shall', 'should'):
            value = f'The receiver {modal} satisfy x = a + b (1-1) where x is the output voltage.'
            units = compare._paragraph_review_units(value, suppressed_table_unit_keys=set())
            self.assertIn(f'The receiver {modal} satisfy', ' '.join(units))

    def test_citation_year_is_not_relaxed_footer(self):
        words, x = [], 180
        for word, width in zip('Clause 9 — International Standards Institute 2024'.split(), (45, 8, 8, 90, 75, 65, 28)):
            if word == '2024':
                x += 15
            words.append((word, x, 920, x + width, 930))
            x += width + 2
        self.assertIsNone(running_footer_folio(words, (0, 0, 600, 1000)))

    def test_other_page_figure_cannot_delete_label_occurrence(self):
        from protocol_pdf_diff.models import SectionChange
        value = 'Alpha Beta Gamma Delta'
        sec = Section('new', '1 Scope', 'Scope', 1, ('1 Scope',), ('1',), 1, 2,
                      value, page_bodies=((1, 'Figure content'), (2, value)))
        change = SectionChange('added', None, sec, 0., added_snippets=[value])
        result = reporting._reader_section_change(change, figure_visual_sides=(False, True),
            figure_visual_texts=((), (value,)), figure_visual_pages=({}, {1: [value]}))
        self.assertIsNotNone(result)
        self.assertEqual([value], result.added_snippets)

    def test_three_same_page_continuations_share_identity(self):
        def table(n):
            return TableVisual(1, n, 'Table 1 Limits' if n == 1 else '',
                (0, (n-1)*25, 100, (n-1)*25+20), '',
                ['表格行: T1 | Parameter=A | Value=1'], '', is_continuation=n>1)
        groups = reporting._table_visual_indexes_by_caption_key([table(n) for n in (1, 2, 3)])
        self.assertEqual([[0, 1, 2]], list(groups.values()))

    def test_frame_cannot_borrow_neighbor_column_caption(self):
        from types import SimpleNamespace
        from protocol_pdf_diff.pdf_extract import _table_fragment_inside_figure_frame
        edges = [dict(orientation='h', x0=300, x1=550, top=y, bottom=y) for y in (100,250)]
        edges += [dict(orientation='v', x0=x, x1=x, top=100, bottom=250) for x in (300,550)]
        words = [dict(text='Figure 1. Response', x0=10, x1=160, top=75, bottom=90)]
        self.assertFalse(_table_fragment_inside_figure_frame(SimpleNamespace(edges=edges),
            (320,120,530,220), ['表格行: T1 | Column 1=Gain(In | Column 2=put)'], words))

    def test_min_max_relationship_does_not_disappear_when_swapped(self):
        old = reporting._table_row_value_display('Parameter=Gain | Min=1 | Max=2 | Unit=dB')
        new = reporting._table_row_value_display('Parameter=Gain | Min=2 | Max=1 | Unit=dB')
        self.assertNotEqual(old, new)
        self.assertIn('Min=1', old)
        self.assertIn('Max=1', new)

    def test_unicode_and_inequality_formula_preserves_modality(self):
        for relation in ('x <=', 'x ≥', 'ΔV ='):
            for modal in ('shall', 'should'):
                value = f'The receiver {modal} satisfy {relation} a + b (1-1)'
                units = compare._paragraph_review_units(value, suppressed_table_unit_keys=set())
                self.assertIn(f'receiver {modal} satisfy', ' '.join(units))

    def test_mismatched_unit_columns_are_not_truncated(self):
        rows = ['Column 1=Frequency | Column 2=Minimum | Column 3=Maximum',
                'Column 1=(GHz) | Column 2=(dB) | Column 3=(dB) | Column 4=(RMS)',
                'Column 1=1 | Column 2=2 | Column 3=3']
        old, new = reporting._remove_one_sided_leading_schema_rows([], rows)
        self.assertEqual(rows, new)

if __name__ == '__main__':
    unittest.main()
