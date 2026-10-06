"""公开比较路径验证重分页、数值/大小写以及出现次数。"""
import unittest
from pathlib import Path
from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText
from protocol_pdf_diff.sectioning import section_document
from protocol_pdf_diff.fallback_repagination import coalesce_exact_fallback_runs


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
