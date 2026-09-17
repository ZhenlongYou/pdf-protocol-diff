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
from protocol_pdf_diff import reporting as reporting_module


def section(sid, body):
    return Section(sid,'1 Receiver','Receiver',1,('1 Receiver',),('1',),1,1,body)

class ScreenshotFirstTests(unittest.TestCase):
    def test_isolated_unchanged_number_cannot_outrank_complete_sentence_context(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for side,value in [('old','100'),('new','120')]:
                doc=fitz.open();page=doc.new_page(width=600,height=300)
                page.insert_text((20,30),'1 Receiver',fontsize=12)
                page.insert_text((20,60),f'The receiver limit is {value} mV in mode A. Additional operating conditions apply.',fontsize=11)
                page.insert_text((20,90),'100 mV',fontsize=11)
                doc.save(root/(side+'.pdf'));doc.close()
            result=run_diff(root/'old.pdf',root/'new.pdf',DiffOptions(visual_watchdog=False))
            old=next(g.old_visuals[0] for g in result.prose_source_visuals if g.old_visuals)
            raster=Image.open(io.BytesIO(base64.b64decode(old.image_data_uri.split(',')[1])))
            with fitz.open(root/'old.pdf') as doc:
                values=[w for w in doc[0].get_text('words') if w[4]=='100']
            for i,word in enumerate(values):
                box=tuple(round(v*raster.size[j%2]/(600 if j%2==0 else 300)) for j,v in enumerate(word[:4]))
                pixels=raster.crop(box).convert('RGB')
                red=max(pixels.getpixel((x,y))[0]-pixels.getpixel((x,y))[1] for x in range(pixels.width) for y in range(pixels.height))
                self.assertGreater(red,10) if i==0 else self.assertLess(red,5)
    def test_similar_unchanged_mode_does_not_receive_changed_mode_highlight(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for side,value in [('old','100'),('new','120')]:
                doc=fitz.open();page=doc.new_page(width=400,height=300)
                page.insert_text((20,30),'1 Receiver',fontsize=12)
                page.insert_text((20,60),f'The receiver limit is {value} mV in mode A.',fontsize=11)
                page.insert_text((20,90),'The receiver limit is 100 mV in mode B.',fontsize=11)
                doc.save(root/(side+'.pdf'));doc.close()
            result=run_diff(root/'old.pdf',root/'new.pdf',DiffOptions(visual_watchdog=False))
            old=next(g.old_visuals[0] for g in result.prose_source_visuals if g.old_visuals)
            self.assertEqual(1,old.highlight_region_count)
            raster=Image.open(io.BytesIO(base64.b64decode(old.image_data_uri.split(',')[1])))
            with fitz.open(root/'old.pdf') as doc:
                values=[w for w in doc[0].get_text('words') if w[4]=='100']
            for i,word in enumerate(values):
                box=tuple(round(v*raster.size[j%2]/(400 if j%2==0 else 300)) for j,v in enumerate(word[:4]))
                red=max(r-g for r,g,b in raster.crop(box).convert('RGB').getdata())
                self.assertGreater(red,10) if i==0 else self.assertLess(red,5)
    def test_appendix_unknown_glyph_is_disclosed_in_reader_formats(self):
        with tempfile.TemporaryDirectory() as d:
            a,b=section('a','Custom glyph \ue123 in mode A.'),section('b','Custom glyph \ue123 in mode B.')
            c=SectionChange('modified',a,b,1.,replaced_snippets=[SnippetPair(a.body,b.body)])
            out=write_reports(DiffResult(Path('o'),Path('n'),[a],[b],[c],[]),d,DiffOptions())
            for key in ('html','markdown','text'):
                value=out[key].read_text();self.assertNotIn('\ue123',value);self.assertIn('U+E123',value)
            self.assertIn('\ue123',out['json'].read_text())
    def test_numeric_one_score_stays_in_primary_formats_without_losing_values(self):
        with tempfile.TemporaryDirectory() as d:
            a,b=section('a','Limit 10 mV.'),section('b','Limit 12 mV.')
            c=SectionChange('modified',a,b,1.,replaced_snippets=[SnippetPair(a.body,b.body)])
            out=write_reports(DiffResult(Path('o'),Path('n'),[a],[b],[c],[]),d,DiffOptions())
            for key,reader in [('html',_read_visible_html_evidence),('markdown',_read_markdown_evidence),('text',_read_text_evidence)]:
                blocks=dict(reader(out[key]).blocks)
                self.assertIn('10 mV', '\n'.join(blocks.values()))
                self.assertIn('12 mV', '\n'.join(blocks.values()))
            self.assertNotIn('<details',out['text'].read_text())
            self.assertIn('12 mV',out['csv'].read_text())
            self.assertEqual([], json.loads(out['json'].read_text())['similarity_review_changes'])
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
    def test_numeric_near_one_is_retained_in_primary_counts(self):
        for score in (1.0,.9996,.9994):
            with self.subTest(score=score),tempfile.TemporaryDirectory() as d:
                old,new=section('old','Limit is 10 mV.'),section('new','Limit is 12 mV.')
                change=SectionChange('modified',old,new,score,replaced_snippets=[SnippetPair(old.body,new.body)])
                out=write_reports(DiffResult(Path('old.pdf'),Path('new.pdf'),[old],[new],[change],[]),d,DiffOptions())
                data=json.loads(out['json'].read_text());html=out['html'].read_text()
                self.assertEqual(1,len(data['content_changes']))
                self.assertEqual([],data['similarity_review_changes'])
                self.assertNotIn('<details class="similarity-review-appendix"',html)
                self.assertIsNone(data['changes'][0]['appendix_card_id'])

    def test_long_section_numeric_delta_is_not_rounded_to_folded_one(self):
        """A single parameter change in a long section must stay visible."""

        with tempfile.TemporaryDirectory() as d:
            stable = (
                " The jitter is measured with a clock from a clock recovery unit "
                "and the receiver shall preserve the specified operating conditions."
            ) * 18
            old_body = (
                "The jitter is measured with a clock from a clock recovery unit "
                "(CRU) (i.e., a first order golden PLL, with corner frequency at "
                "fb /26450, and a 20 dB/decade slope, see Section 1.6) as the "
                "trigger or reference clock."
                + stable
            )
            new_body = old_body.replace("fb /26450", "fb /26560")
            old,new=section('old',old_body),section('new',new_body)
            change=SectionChange(
                'modified', old, new, .999643,
                replaced_snippets=[SnippetPair(old_body[:220], new_body[:220])],
            )
            out=write_reports(
                DiffResult(Path('old.pdf'),Path('new.pdf'),[old],[new],[change],[]),
                d, DiffOptions(),
            )
            data=json.loads(out['json'].read_text())
            html=out['html'].read_text()
            self.assertEqual(1, len(data['content_changes']))
            self.assertEqual([], data['similarity_review_changes'])
            visible=data['content_changes'][0]
            self.assertAlmostEqual(.999643, visible['pair_similarity'], places=6)
            self.assertLess(visible['content_similarity'], 1.0)
            self.assertFalse(visible['critical_content_equal'])
            self.assertIn('26450', html)
            self.assertIn('26560', html)
            self.assertIn('内容相似度', html)

    def test_critical_identifier_and_operator_deltas_stay_in_primary_list(self):
        """Identifiers and comparison operators are protected like numbers."""

        cases = (
            ("Mode=SAFE", "Mode=FAST"),
            ("The limit <= 10 mV.", "The limit >= 10 mV."),
        )
        for old_text, new_text in cases:
            with self.subTest(old_text=old_text), tempfile.TemporaryDirectory() as d:
                old, new = section("old", old_text), section("new", new_text)
                change = SectionChange(
                    "modified", old, new, 1.0,
                    replaced_snippets=[SnippetPair(old_text, new_text)],
                )
                out = write_reports(
                    DiffResult(Path("old.pdf"), Path("new.pdf"), [old], [new], [change], []),
                    d,
                    DiffOptions(),
                )
                data = json.loads(out["json"].read_text())
                self.assertEqual(1, len(data["content_changes"]))
                self.assertEqual([], data["similarity_review_changes"])
                self.assertFalse(data["content_changes"][0]["critical_content_equal"])

    def test_critical_delta_without_local_snippet_is_not_marked_equal(self):
        """The full-body fallback protects identifiers when snippets are absent."""

        old, new = section("old", "Mode=SAFE"), section("new", "Mode=FAST")
        change = SectionChange("modified", old, new, 1.0)
        self.assertTrue(reporting_module._section_change_has_critical_delta(change))
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
            self.assertNotEqual(
                group.old_visuals[0].image_data_uri,
                group.old_visuals[0].raw_image_data_uri,
            )
            self.assertNotEqual(
                group.new_visuals[0].image_data_uri,
                group.new_visuals[0].raw_image_data_uri,
            )
            self.assertIn('<details class="prose-text-details">',html)
            # Main cards keep the text facts; the shared page group owns the
            # visible old/new source screenshots above them.
            self.assertLess(html.index('class="page-evidence-source-grid"'),html.index('<details class="prose-text-details">'))

    def test_shared_page_group_keeps_both_sides_for_every_matched_card(self):
        """Multiple matched findings share one old/new screenshot pair."""

        def at_page(sid, title, body, page):
            return Section(
                sid,
                title,
                title,
                1,
                (title,),
                ("1",),
                page,
                page,
                body,
            )

        old_first = at_page("old-first", "1 First", "The limit is 100 mV.", 7)
        new_first = at_page("new-first", "1 First", "The limit is 120 mV.", 9)
        old_second = at_page("old-second", "2 Second", "The mode is legacy.", 7)
        new_second = at_page("new-second", "2 Second", "The mode is revised.", 9)
        changes = [
            SectionChange(
                "modified",
                old_first,
                new_first,
                0.9,
                replaced_snippets=[SnippetPair(old_first.body, new_first.body)],
            ),
            SectionChange(
                "modified",
                old_second,
                new_second,
                0.9,
                replaced_snippets=[SnippetPair(old_second.body, new_second.body)],
            ),
        ]
        old_visual = ProseSourceVisual(
            7,
            (0.0, 0.0, 100.0, 100.0),
            "data:image/png;base64,old-shared",
            1,
            1,
            source_view_box=(0.0, 0.0, 100.0, 100.0),
        )
        new_visual = replace(old_visual, page_number=9, image_data_uri="data:image/png;base64,new-shared")
        visuals = [
            ProseSourceVisualGroup("modified", "old-first", "new-first", (old_visual,), (new_visual,)),
            ProseSourceVisualGroup("modified", "old-second", "new-second", (old_visual,), (new_visual,)),
        ]
        result = DiffResult(
            Path("old.pdf"),
            Path("new.pdf"),
            [old_first, old_second],
            [new_first, new_second],
            changes,
            [],
            prose_source_visuals=visuals,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            html = write_reports(result, Path(temp_dir), DiffOptions())["html"].read_text()

        self.assertEqual(1, html.count('class="page-evidence-source-grid"'))
        self.assertEqual(1, html.count('id="page-source-old-7"'))
        self.assertEqual(1, html.count('id="page-source-new-9"'))
        self.assertEqual(1, html.count('src="data:image/png;base64,old-shared"'))
        self.assertEqual(1, html.count('src="data:image/png;base64,new-shared"'))
        for card_id in ("change-1", "change-2"):
            start = html.index(f'<section class="change-card" id="{card_id}"')
            end = html.index("</section>", start)
            self.assertNotIn("<img", html[start:end])
        self.assertIn("The limit is", html)
        self.assertIn("The mode is", html)

    def test_sentence_punctuation_only_pdf_change_is_ignored_end_to_end(self):
        """Ordinary prose punctuation must not become a report-level delta."""

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old = write_multipage_text_pdf(
                root / "old.pdf",
                [[
                    "1 Receiver",
                    "A CEI implementation complies to the specifications of this clause over the range of baud rates; stated for the implementation within this range.",
                ]],
            )
            new = write_multipage_text_pdf(
                root / "new.pdf",
                [[
                    "1 Receiver",
                    "A CEI implementation complies to the specifications of this clause over the range of baud rates stated for the implementation within this range.",
                ]],
            )

            result = run_diff(old, new, DiffOptions(visual_watchdog=False))
            outputs = write_reports(result, root / "report", DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            html = outputs["html"].read_text(encoding="utf-8")

            self.assertEqual([], result.changes)
            self.assertEqual([], payload["content_changes"])
            self.assertNotIn("展开文字识别明细", html)
            self.assertNotIn("<mark", html)

if __name__=='__main__': unittest.main()
