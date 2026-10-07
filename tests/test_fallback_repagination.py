"""公开比较路径验证重分页、数值/大小写以及出现次数。"""
import unittest
from dataclasses import replace
from pathlib import Path
from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText
from protocol_pdf_diff.sectioning import section_document
from protocol_pdf_diff.fallback_repagination import coalesce_exact_fallback_runs, coalesce_anchored_fallback_runs


def extraction(lines):
    return ExtractionResult(Path('manual.pdf'),[PageText(i+1,t) for i,t in enumerate(lines)],total_pages=len(lines))


class FallbackRepaginationTests(unittest.TestCase):
    a='the receiver retains every declared operating condition with limit +3.0 V.'
    b='the command ENABLE sets the output for the entire operating interval.'
    c='the controller records each measurement after the selected interval ends.'

    def test_two_to_three_pages_keep_sources_and_do_not_create_differences(self):
        old=extraction([self.a+' '+self.b,self.c])
        # 在单词之间分页；不能因分词破坏输入预期。
        new=extraction([self.a,'the command ENABLE sets the output', 'for the entire operating interval. '+self.c])
        result=compare_extractions(old,new,DiffOptions(include_unchanged_sections=True))
        self.assertEqual(['unchanged'],[c.change_type for c in result.changes])
        change=result.changes[0]
        self.assertEqual((1,2),(change.old_section.start_page,change.old_section.end_page))
        self.assertEqual((1,3),(change.new_section.start_page,change.new_section.end_page))
        self.assertEqual(tuple((p.page_number,p.text) for p in new.pages),change.new_section.page_bodies)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_signed_values_case_count_and_order_cannot_be_coalesced(self):
        old=extraction([self.a,self.b,self.c])
        for texts in ([self.a.replace('+3.0','-3.0'),self.b,self.c],
                      [self.a,self.b.replace('ENABLE','enable'),self.c],
                      [self.a,self.c,self.b], [self.a,self.b,self.b,self.c]):
            with self.subTest(texts=texts):
                new=extraction(texts)
                before=section_document(old);after=section_document(new)
                left,right=coalesce_exact_fallback_runs(before,after)
                self.assertIs(before,left);self.assertIs(after,right)
                result=compare_extractions(old,new,DiffOptions())
                self.assertTrue(result.changes)

    def test_repagination_with_one_literal_edit_reports_only_the_edit(self):
        old = extraction([self.a+' '+self.b, self.c])
        for before, after in (('+3.0', '+3.5'), ('ENABLE', 'enable'), ('sets', 'does not set')):
            text = (self.a+' '+self.b+' '+self.c).replace(before, after)
            words = text.split()
            new = extraction([' '.join(words[:11]), ' '.join(words[11:25]), ' '.join(words[25:])])
            with self.subTest(edit=after):
                result = compare_extractions(old, new, DiffOptions())
                self.assertEqual(['modified'], [c.change_type for c in result.changes])
                change = result.changes[0]
                self.assertEqual([], change.added_snippets)
                self.assertEqual([], change.removed_snippets)
                self.assertEqual(1, len(change.replaced_snippets))
                self.assertIn(before, change.replaced_snippets[0].old)
                self.assertIn(after, change.replaced_snippets[0].new)
                self.assertEqual(tuple((p.page_number,p.text) for p in new.pages), change.new_section.page_bodies)
                self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_anchored_path_refuses_movements_multiple_edits_and_repeated_anchors(self):
        old = section_document(extraction([self.a+' '+self.b, self.c]))
        cases = ([self.a, self.c, self.b],
                 [self.a.replace('retains','does not retain'), self.b, self.c],  # 文首没有足够前锚点。
                 [self.a.replace('+3.0','+3.5'), self.b.replace('ENABLE','enable'), self.c],
                 [self.a, self.b, self.b+' '+self.c])
        for texts in cases:
            new = section_document(extraction(texts))
            left, right = coalesce_anchored_fallback_runs(old, new)
            self.assertIs(left, old)
            self.assertIs(right, new)
        repeated = self.a+' '+self.a
        left = section_document(extraction([repeated, self.b+' '+self.c]))
        right = section_document(extraction([self.a.replace('+3.0','+3.5'), self.a, self.b+' '+self.c]))
        self.assertIs(left, coalesce_anchored_fallback_runs(left, right)[0])

    def test_anchored_path_refuses_role_change_and_page_gap(self):
        left = section_document(extraction([self.a+' '+self.b, self.c]))
        right = section_document(extraction([self.a.replace('+3.0','+3.5'), self.b, self.c]))
        for candidate in ([replace(s, role='document_metadata') for s in right],
                          [right[0], replace(right[1], start_page=5, end_page=5), right[2]]):
            self.assertIs(left, coalesce_anchored_fallback_runs(left, candidate)[0])

    def test_unit_on_next_page_does_not_become_a_new_list_item(self):
        old = extraction([self.a+' '+self.b, self.c])
        new = extraction([self.a.replace('+3.0','+3.5').removesuffix(' V.'), 'V. '+self.b, self.c])
        result = compare_extractions(old, new, DiffOptions())
        self.assertEqual(1,len(result.changes))
        change=result.changes[0]
        self.assertEqual(1,len(change.replaced_snippets))
        self.assertEqual(self.a.replace('+3.0','+3.5'),change.replaced_snippets[0].new)
        self.assertTrue(change.new_section.page_bodies[1][1].startswith('V.'))
