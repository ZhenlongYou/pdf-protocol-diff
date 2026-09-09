"""User-visible screenshot-first report contract and retained review appendix."""
import json
import re
from dataclasses import replace
from unittest.mock import patch
import base64
import io
import fitz
from PIL import Image
from pathlib import Path
import tempfile
import unittest
from protocol_pdf_diff.models import DiffResult,DiffOptions,Section,SectionChange,SnippetPair
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.compare import run_diff
from protocol_pdf_diff.sample_data import write_multipage_text_pdf
from protocol_pdf_diff.models import TableVisual, TableChange, TableRowChange, ProseSourceVisual, ProseSourceVisualGroup
from protocol_pdf_diff.screenshot_presentation import table_context_image
from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.prose_source_visuals import build_prose_source_visuals, _assign_snippets_to_pages, _SnippetHighlight
from protocol_pdf_diff.accuracy_evaluation import _read_visible_html_evidence, _read_markdown_evidence, _read_text_evidence


def section(sid, body):
    return Section(sid,'1 Receiver','Receiver',1,('1 Receiver',),('1',),1,1,body)

class ScreenshotFirstTests(unittest.TestCase):
    def test_appendix_scopes_are_reviewable_in_all_formats_without_losing_values(self):
        with tempfile.TemporaryDirectory() as d:
            a,b=section('a','Limit 10 mV.'),section('b','Limit 12 mV.')
            c=SectionChange('modified',a,b,1.,replaced_snippets=[SnippetPair(a.body,b.body)])
            out=write_reports(DiffResult(Path('o'),Path('n'),[a],[b],[c],[]),d,DiffOptions())
            for key,reader in [('html',_read_visible_html_evidence),('markdown',_read_markdown_evidence),('text',_read_text_evidence)]:
                blocks=dict(reader(out[key]).blocks)
                self.assertIn('10 mV',blocks['A-C1']);self.assertIn('12 mV',blocks['A-C1'])
            self.assertNotIn('<details',out['text'].read_text())
            self.assertIn('12 mV',out['similarity_review_csv'].read_text())
    def test_same_page_repeated_short_sentence_is_not_highlighted_twice(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);body='The receiver limit is 100 mV.'
            a=write_multipage_text_pdf(root/'a.pdf',[['1 Receiver',body,body]])
            b=write_multipage_text_pdf(root/'b.pdf',[['1 Receiver','The receiver limit is 120 mV.',body]])
            ea,eb=extract_pdf_text(a),extract_pdf_text(b)
            old,new=section('a',body+' '+body),section('b','The receiver limit is 120 mV. '+body)
            result=DiffResult(a,b,[old],[new],[SectionChange('modified',old,new,.8,replaced_snippets=[SnippetPair(body,'The receiver limit is 120 mV.')])],[])
            groups,warnings=build_prose_source_visuals(result,ea,eb)
            self.assertEqual([],warnings);self.assertTrue(groups[0].old_visuals)
            self.assertEqual(0,sum(v.highlight_region_count for v in groups[0].old_visuals))
    def test_missing_one_snippet_retains_all_candidate_pages_and_does_not_cap_three(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            a=write_multipage_text_pdf(root/'a.pdf',[['1 Receiver' if i==0 else 'Continuation',f'The receiver mode {i} has a limit of 100 mV.'] for i in range(5)])
            b=write_multipage_text_pdf(root/'b.pdf',[['1 Receiver','The receiver mode 0 has a limit of 120 mV.']])
            ea,eb=extract_pdf_text(a),extract_pdf_text(b)
            old=replace(section('a','The receiver mode 0 has a limit of 100 mV.'),end_page=5,page_bodies=tuple((i+1,p.text) for i,p in enumerate(ea.pages)))
            new=section('b','The receiver mode 0 has a limit of 120 mV.')
            change=SectionChange('modified',old,new,.7,replaced_snippets=[SnippetPair(old.body,new.body)],removed_snippets=['ZZZ qqq unknown extracted fragment.'])
            groups,_=build_prose_source_visuals(DiffResult(a,b,[old],[new],[change],[]),ea,eb)
            self.assertEqual([1,2,3,4,5],[v.page_number for v in groups[0].old_visuals])
            self.assertEqual(0,groups[0].old_omitted_page_count)
    def test_table_view_keeps_semantic_bbox_and_highlights_only_unique_changed_word(self):
        image=Image.new('RGB',(300,400),'white');buf=io.BytesIO();image.save(buf,format='PNG')
        uri='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()
        a=TableVisual(1,1,'Limit',(20,100,200,130),'',[], '',row_alignment_reliable=True,
                      context_image_data_uri=uri,context_bbox=(0,0,300,400),context_words=(('10',100,110,120,120),('mV',125,110,140,120)))
        b=replace(a,context_words=(('12',100,110,120,120),('mV',125,110,140,120)))
        c=TableChange('modified',(a,),(b,),.8,False,(TableRowChange('Limit','10 mV','12 mV','实质变化'),))
        rendered,colored=table_context_image(a,c,'old');self.assertTrue(colored)
        pixels=Image.open(io.BytesIO(base64.b64decode(rendered.split(',')[1])))
        self.assertEqual((300,400),pixels.size);self.assertEqual((20,100,200,130),a.bbox)
        self.assertEqual((255,255,255),pixels.getpixel((30,30)))
        r,g,b=pixels.getpixel((110,115));self.assertGreater(r,g)
        self.assertFalse(table_context_image(replace(a,row_alignment_reliable=False),c,'old')[1])
        repeated=replace(a,context_words=(*a.context_words,('10',150,110,170,120)))
        self.assertFalse(table_context_image(repeated,replace(c,old_tables=(repeated,)),'old')[1])
    def test_displayed_one_is_retained_in_appendix_not_primary_counts(self):
        for score in (1.0,.9996,.9994):
            with self.subTest(score=score),tempfile.TemporaryDirectory() as d:
                old,new=section('old','Limit is 10 mV.'),section('new','Limit is 12 mV.')
                change=SectionChange('modified',old,new,score,replaced_snippets=[SnippetPair(old.body,new.body)])
                out=write_reports(DiffResult(Path('old.pdf'),Path('new.pdf'),[old],[new],[change],[]),d,DiffOptions())
                data=json.loads(out['json'].read_text());html=out['html'].read_text()
                if score>=.9995:
                    self.assertEqual([],data['content_changes'])
                    self.assertEqual(1,len(data['similarity_review_changes']))
                    self.assertIn('<details class="similarity-review-appendix"',html)
                    text=re.sub('<[^>]+>', '', html)
                    self.assertIn('10 mV',text);self.assertIn('12 mV',text)
                    self.assertIsNotNone(data['changes'][0]['appendix_card_id'])
                else:
                    self.assertEqual(1,len(data['content_changes']))
                    self.assertEqual([],data['similarity_review_changes'])
    def test_added_default_one_remains_primary(self):
        with tempfile.TemporaryDirectory() as d:
            new=section('new','The receiver shall support 12 mV.')
            out=write_reports(DiffResult(Path('old.pdf'),Path('new.pdf'),[],[new],[SectionChange('added',None,new,1,added_snippets=[new.body])],[]),d,DiffOptions())
            data=json.loads(out['json'].read_text());self.assertEqual(1,len(data['content_changes']))
    def test_short_real_pdf_change_has_full_context_and_folded_text(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            old=write_multipage_text_pdf(root/'old.pdf',[['1 Receiver','The receiver limit is 100 mV.','Following context remains visible.']])
            new=write_multipage_text_pdf(root/'new.pdf',[['1 Receiver','The receiver limit is 120 mV.','Following context remains visible.']])
            result=run_diff(old,new,DiffOptions(visual_watchdog=False))
            out=write_reports(result,root/'report',DiffOptions());html=out['html'].read_text()
            group=next(g for g in result.prose_source_visuals if g.old_visuals)
            self.assertGreater(group.old_visuals[0].highlight_region_count,0)
            self.assertEqual((0.,0.),group.old_visuals[0].crop_bbox[:2])
            self.assertIn('<details class="prose-text-details">',html)
            self.assertLess(html.index('class="prose-source-visual"'),html.index('<details class="prose-text-details">'))

if __name__=='__main__': unittest.main()
