"""Reader tasks: literal facts first, unique source navigation, no lost rows."""
from dataclasses import replace
import base64
import html
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

import fitz
from PIL import Image, ImageDraw
from protocol_pdf_diff.models import DiffOptions, ProseSourceVisual, ProseSourceVisualGroup, Section, SectionChange, SnippetPair, TableChange, TableRowChange, TableVisual
from protocol_pdf_diff.reader_focus import difference_windows, locate_source, delta_spans
from protocol_pdf_diff.reporting import _build_page_source_aliases, _build_prose_source_aliases, _inline_tokens, _render_change_html, _render_table_change_html, _render_visual_review_item_html, _section_change_page_sort_key, _table_change_page_sort_key, write_reports
from protocol_pdf_diff.visual_watchdog import _compare_page_images
from protocol_pdf_diff.compare import run_diff
from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.prose_source_visuals import _owned_crop_words
from protocol_pdf_diff.models import DiffResult


def section(text, page=12, sid='s'):
    return Section(sid,'1 Receiver acceptance','Receiver acceptance',1,('1 Receiver acceptance',),('1',),page,page,text)


def source(text, page=12, y=30):
    words=[];x=10
    for token in text.split():
        words.append((x,y,x+len(token)*6,y+12,token));x+=len(token)*6+5
    im=Image.new('RGB',(600,100),'white');buf=io.BytesIO();im.save(buf,format='PNG')
    return ProseSourceVisual(page,(0,0,600,100),'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode(),0,1,source_words=tuple(words),source_view_box=(0,0,600,100))


class ReaderFocusTests(unittest.TestCase):
    def test_table_and_prose_same_page_share_one_source_screenshot(self):
        table = TableVisual(8, 1, "Table 1", (0, 0, 100, 100), source("table").image_data_uri, [], "")
        table_change = TableChange("modified", (table,), (), 0.9, False, ())
        prose_group = ProseSourceVisualGroup("modified", "old", "new", old_visuals=(source("text", 8),))
        aliases = _build_page_source_aliases(
            [("table-1", table_change)],
            [("change-1", prose_group)],
        )
        self.assertEqual(
            {("old", 0): "table-change-1-source-old-0"},
            aliases["prose"]["change-1"],
        )
        self.assertEqual({}, aliases["table"])

    def test_table_repeated_page_uses_one_visible_image_and_keeps_alias(self):
        image = source("table", 8).image_data_uri
        first = TableVisual(8, 1, "Table 1", (0, 0, 100, 100), image, [], "")
        second = TableVisual(8, 2, "Table 2", (0, 0, 100, 100), image, [], "")
        change = TableChange("modified", (first, second), (), 0.9, False, ())
        aliases = _build_page_source_aliases([("table-1", change)], [])
        rendered = _render_table_change_html(1, change, source_page_aliases=aliases["table"]["table-1"])
        self.assertEqual(1, rendered.count("<img"))
        self.assertIn('class="prose-source-alias-anchor table-source-alias-anchor"', rendered)
        self.assertIn('data-source-alias="table-change-1-source-old-0"', rendered)
        self.assertNotIn("本页截图已在其他差异证据展示", rendered)

    def test_page_sort_key_uses_page_then_table_before_text(self):
        text_change = SectionChange("modified", section("old", 8), section("new", 8, sid="new"), 0.9)
        table = TableVisual(8, 1, "Table 1", (0, 0, 100, 100), "", [], "")
        table_change = TableChange("modified", (table,), (), 0.9, False, ())
        table_key = _table_change_page_sort_key(table_change)
        text_key = _section_change_page_sort_key(text_change)
        self.assertEqual(8, table_key[0])
        self.assertEqual(8, text_key[0])
        self.assertLess((table_key[0], 0, table_key[1], table_key[2]), (text_key[0], 1, text_key[1], text_key[2]))

    def test_fully_reused_prose_sources_keep_hidden_aliases_without_images(self):
        change = SectionChange("modified", section("old", 8), section("new", 8, sid="new"), 0.9)
        group = ProseSourceVisualGroup("modified", "old", "new", old_visuals=(source("old", 8),), new_visuals=(source("new", 9),))
        rendered = _render_change_html(
            1,
            change,
            prose_source_visual=group,
            source_visual_aliases={
                ("old", 0): "table-change-1-source-old-0",
                ("new", 0): "table-change-1-source-new-0",
            },
        )
        self.assertNotIn("prose-source-visual-grid", rendered)
        self.assertEqual(0, rendered.count("<img"))
        self.assertEqual(2, rendered.count("prose-source-alias-anchor"))
        self.assertNotIn("本页截图已在其他变化项展示", rendered)
        self.assertNotIn("本页左右对比证据中已展示截图", rendered)
        self.assertIn('data-source-alias="table-change-1-source-old-0"', rendered)
        self.assertIn('data-source-alias="table-change-1-source-new-0"', rendered)

    def test_reused_prose_source_alias_keeps_focus_without_duplicate_image(self):
        """A reused source occurrence keeps its focus target without a second image."""

        visual = source("old", 8)
        group = ProseSourceVisualGroup(
            "modified", "old", "new", old_visuals=(visual,), new_visuals=()
        )
        rendered = _render_change_html(
            2,
            SectionChange("modified", section("old", 8), None, 0.9),
            prose_source_visual=group,
            source_visual_aliases={("old", 0): "change-1-source-old-0"},
        )
        self.assertEqual(0, rendered.count("<img"))
        self.assertIn('id="change-2-source-old-0"', rendered)
        self.assertIn('data-source-alias="change-1-source-old-0"', rendered)
        self.assertNotIn("本页截图已在其他变化项展示", rendered)
        self.assertNotIn("跳转到已展示截图", rendered)

    def test_neutral_repeated_source_page_points_to_highlighted_canonical(self):
        highlighted = replace(source("changed value", 8), highlight_region_count=1)
        first = ProseSourceVisualGroup(
            "modified", "old-a", "new-a", old_visuals=(highlighted,)
        )
        second = ProseSourceVisualGroup(
            "modified", "old-b", "new-b", old_visuals=(source("context", 8),)
        )
        aliases = _build_prose_source_aliases(
            [("change-1", first), ("change-2", second)]
        )
        self.assertEqual(
            {("old", 0): "change-1-source-old-0"}, aliases["change-2"]
        )

    def test_compact_changes_keep_complete_written_numbers_and_glyph_uncertainty(self):
        for old,new in (('3.0 V','2.5 V'),('+1.50 mV','-1.50 mV'),('1e-6','1e-9'),('1.50 mV','-1.50 mV'),('-1.50 mV','1.50 mV'),('3 V','3.5 V')):
            spans=list(delta_spans(old,new))
            self.assertEqual(1,len(spans))
            a,b=spans[0]
            self.assertEqual(old.split()[0],old[a[0]:a[1]])
            self.assertEqual(new.split()[0],new[b[0]:b[1]])
        old='The receiver shall use \uf061 at 3.0 V during Mode A.'
        new='The receiver shall use α at 2.5 V during Mode A.'
        change=SectionChange('modified',section(old),section(new,sid='n'),.9,replaced_snippets=[SnippetPair(old,new)])
        rendered=_render_change_html(1,change)
        self.assertNotIn('\uf061',rendered)
        self.assertIn('字符待核实',rendered)
        self.assertIn('<del>3.0</del>',rendered)
        self.assertIn('<ins>2.5</ins>',rendered)
        self.assertIn('during Mode A.',rendered)
        old='The receiver shall use \ue111 at 3.0 V.'
        new='The receiver shall use \ue112 at 3.0 V.'
        rendered=_render_change_html(1,SectionChange('modified',section(old),section(new,sid='n'),.9,replaced_snippets=[SnippetPair(old,new)]))
        self.assertNotIn('\ue111',rendered);self.assertNotIn('\ue112',rendered)
        self.assertNotIn('<del>',rendered);self.assertIn('待核实',rendered)

    def test_excluding_not_never_manufactures_a_source_phrase(self):
        extracted=extract_pdf_text(Path(__file__).parent/'fixtures/reader_focus/excluded-word-bridge.pdf')
        page=extracted.pages[0]
        exclusion=tuple((x0,y0,x1,y1) for b in page.blocks for text,x0,y0,x1,y1 in b.word_boxes if text=='NOT')
        self.assertEqual(1,len(exclusion))
        crop=(30,140,400,190)
        words=_owned_crop_words(page,crop,exclusion,page.text)
        visual=replace(source('unused'),page_number=1,crop_bbox=crop,source_words=words,source_view_box=crop)
        self.assertIsNone(locate_source('Receiver limit = 8 mA.',(visual,),'old',page.text))

    def test_condition_prefix_is_visible_and_late_audit_occurrences_are_reachable(self):
        prefix='Under Mode A, after all channels have completed their prescribed training sequence and every calibration record has been reviewed by the controller, '
        old=prefix+'the receiver shall use a voltage limit of +1.50 mV.'
        new=prefix+'the receiver shall use a voltage limit of -1.50 mV.'
        windows=difference_windows(old,new,_inline_tokens)
        self.assertEqual([(old,new)],windows)
        pairs=[SnippetPair(f'For condition {n}, limit is {n+10} mA.',f'For condition {n}, limit is {n+11} mA.') for n in range(23)]
        pairs[-1]=SnippetPair('I_trip ≥ 95 mA at -20 °C','I_trip ≥ 105 mA at -20 °C')
        a=section(' '.join(p.old for p in pairs));b=section(' '.join(p.new for p in pairs),sid='new')
        c=SectionChange('modified',a,b,.9,replaced_snippets=pairs[:20],omitted_snippet_count=3,audit_replaced_snippets=pairs)
        with tempfile.TemporaryDirectory() as tmp:
            out=write_reports(DiffResult(Path('old.pdf'),Path('new.pdf'),[a],[b],[c],[]),tmp,DiffOptions())
            rendered=out['html'].read_text(encoding='utf-8')
            self.assertIn('23 条明细',rendered)
            self.assertIn('其余 18 条变化明细',rendered)
            self.assertIn('105',rendered);self.assertIn('-20',rendered)
            self.assertNotIn('另有 3 条片段未展示',rendered)
            self.assertEqual(23,len(json.loads(out['json'].read_text(encoding='utf-8'))['changes'][0]['replaced_snippets']))

    def test_literal_windows_preserve_operators_units_and_conditions(self):
        old='SER ≤ 1e-6 at 85 °C; clock = 10 GHz'
        new='SER < 1e-9 at 105 °C; clock = 10 MHz'
        self.assertEqual([(old,new)],difference_windows(old,new,_inline_tokens))
        change=SectionChange('modified',section(old),section(new,sid='n'),.9,replaced_snippets=[SnippetPair(old,new)])
        text=html.unescape(re.sub('<[^>]+>','',_render_change_html(1,change)))
        for term in ('≤','1e-6','85 °C','GHz','1e-9','105 °C','MHz'):
            self.assertIn(term,text)
        self.assertNotIn('性能提升',text)

    def test_screenshots_precede_folded_focus_and_audit_stays_available(self):
        old='Rail A during Startup: +1.50 mV';new='Rail A during Startup: -1.50 mV'
        change=SectionChange('modified',section(old),section(new,sid='n'),.95,replaced_snippets=[SnippetPair(old,new)])
        group=ProseSourceVisualGroup('modified','s','n',(source(old),),(source(new,14),))
        rendered=_render_change_html(1,change,prose_source_visual=group)
        self.assertLess(rendered.index('class="prose-source-visual"'),rendered.index('class="reader-focus"'))
        self.assertIn('定位对应原文',rendered)
        self.assertIn('展开文字识别明细',rendered)
        self.assertEqual(1,len(change.replaced_snippets))

    def test_unique_physical_source_not_array_order(self):
        a=source('limit = 8 mA',12);b=source('timeout = 12 ms',13)
        target=locate_source('limit = 8 mA',(b,a),'old','limit = 8 mA timeout = 12 ms')
        self.assertEqual(('old-1',12), (target['id'],target['page']))
        # Same physical crop replay is not a second source occurrence.
        self.assertEqual(12,locate_source('limit = 8 mA',(a,a),'old','limit = 8 mA')['page'])
        # The two actual occurrences cannot be called one unique changed position.
        self.assertIsNone(locate_source('limit = 8 mA',(a,source('limit = 8 mA',13)),'old','limit = 8 mA'))
        # A repeated occurrence on an omitted screenshot page still prevents focus.
        self.assertIsNone(locate_source('limit = 8 mA',(a,),'old','limit = 8 mA limit = 8 mA'))
        self.assertIsNone(locate_source('limit = 8 mA',(replace(a,source_words=()),),'old','limit = 8 mA'))
        self.assertIsNone(locate_source('limit = -8 mA',(a,),'old','limit = -8 mA'))

    def test_review_remains_neutral_and_has_specific_reason(self):
        old='Mode A limit = 8 mA';new='Mode A limit = 9 mA'
        change=SectionChange('review',section(old),section(new,sid='n'),.3,replaced_snippets=[SnippetPair(old,new)],review_reason='Mode A 的对应关系未确定')
        rendered=_render_change_html(1,change)
        self.assertIn(change.review_reason,rendered)
        self.assertNotIn('<ins>',rendered);self.assertNotIn('<del>',rendered)
        self.assertIn('待核实',rendered)

    def test_all_table_rows_remain_reachable_beyond_twenty(self):
        rows=tuple(TableRowChange(f'Mode {n}',f'{n} mA',f'{n+1} mA','实质变化') for n in range(23))
        rows+=(TableRowChange('Multiline ownership','unverified','unverified','需人工复核'),)
        change=TableChange('modified',(),(),1.,False,rows)
        rendered=_render_table_change_html(1,change)
        for row in rows:
            self.assertEqual(1,rendered.count(f'<td>{row.item}</td>'))
        self.assertIn('全部保留',rendered)
        self.assertLess(rendered.index('class="table-shot-grid"'),rendered.index('class="table-row-summary"'))

    def test_visual_targets_cover_distant_pixels_without_claiming_semantics(self):
        old=Image.new('RGB',(240,320),'white');new=old.copy();draw=ImageDraw.Draw(new)
        draw.rectangle((30,40,45,55),fill='black');draw.rectangle((180,250,200,270),fill='black')
        item=_compare_page_images(old,new,old_page_number=3,new_page_number=5,alignment_method='same-page-text')
        self.assertEqual(2,len(item.focus_regions))
        # Full-width preview keeps x identity; its top is derived from actual crop.
        self.assertEqual(30,item.focus_regions[0][0]);self.assertEqual(201,item.focus_regions[1][2])
        self.assertEqual((240,item.preview_size[1]),item.preview_size)
        self.assertGreater(item.focus_regions[1][1],item.focus_regions[0][3])
        rendered=_render_visual_review_item_html(7,item)
        self.assertIn('核对区域 1',rendered);self.assertIn('核对区域 2',rendered)
        self.assertIn('不代表技术要求已改变',rendered)
        self.assertIn('visual-7-old',rendered);self.assertIn('visual-7-new',rendered)
        unchanged=_compare_page_images(old,old,old_page_number=3,new_page_number=5,alignment_method='same-page-text')
        self.assertIsNone(unchanged)

    def test_real_pdf_compare_and_report_preserve_numeric_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for side,value in [('old','7.5'),('new','8.5')]:
                with fitz.open() as doc:
                    p=doc.new_page();p.insert_text((60,60),'1 Receiver Requirements',fontsize=16)
                    body=' '.join(f'During phase {n}, the startup receiver shall use a limit of {float(value)+n*10:.1f} mA while the reference clock remains stable and the supply is calibrated.' for n in range(8))
                    p.insert_textbox(fitz.Rect(60,90,520,730),body,fontsize=11)
                    p=doc.new_page();p.insert_text((60,60),'2 Diagram');p.draw_rect(fitz.Rect(80,120,140,170),fill=(0,0,1) if side=='old' else (1,0,0))
                    doc.save(root/f'{side}.pdf')
            result=run_diff(root/'old.pdf',root/'new.pdf',DiffOptions())
            reports=write_reports(result,root/'reports',DiffOptions())
            rendered=reports['html'].read_text(encoding='utf-8');data=json.loads(reports['json'].read_text(encoding='utf-8'))
            self.assertIn('先看具体变化',rendered)
            self.assertIn('7.5',rendered);self.assertIn('8.5',rendered)
            self.assertIn('data-focus-targets',rendered)
            targets=[json.loads(html.unescape(value)) for value in re.findall(r'data-focus-targets="([^"]+)"',rendered)]
            self.assertTrue(any(t and t['id'].startswith('change-') for pair in targets for t in pair.values()), 'real prose must have its own source navigation, not only visual buttons')
            self.assertIn('preview_size',data['visual_review_items'][0])
            self.assertTrue(any(v.source_words for group in result.prose_source_visuals for v in group.old_visuals))
