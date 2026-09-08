"""Independent source and user-facing regression cases for stable comparison."""
from dataclasses import replace
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import fitz
from protocol_pdf_diff.compare import run_diff, compare_extractions
from protocol_pdf_diff.glyph_evidence import empty_truetype_glyphs, EmptyGlyphFont, EvidencePage
from protocol_pdf_diff.models import DiffOptions
from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.prose_source_visuals import _crop_regions, _change_highlights
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.sample_data import write_multipage_text_pdf
from protocol_pdf_diff.sectioning import section_document

FIXTURES = Path(__file__).parent / 'fixtures/source_evidence'


class StableSourceEvidenceTests(unittest.TestCase):
    def test_source_span_uniqueness_counts_words_not_proof_routes(self):
        from protocol_pdf_diff.heading_evidence import line_word_evidence
        original=('2 Requirements',72.,70.,160.,84.)
        elsewhere=('2 Requirements',340.,70.,428.,84.)
        span=[(original,('Helvetica-Bold',14.))]
        with patch('protocol_pdf_diff.heading_evidence._page_word_index',
                   return_value=({'2 Requirements':[span,list(span)]},{},())):
            self.assertEqual(span,line_word_evidence(object(),'2 Requirements'))
        with patch('protocol_pdf_diff.heading_evidence._page_word_index',
                   return_value=({'2 Requirements':[span,[(elsewhere,('Helvetica-Bold',14.))]]},{},())):
            self.assertEqual([],line_word_evidence(object(),'2 Requirements'))

    def test_end_numbers_and_mixed_footer_versions_remain_observable(self):
        for old,new,owner in [
            ('bottom_requirement-120.pdf','bottom_requirement-150.pdf','1 Receiver'),
            ('long-numeric-revision-1.pdf','long-numeric-revision-2.pdf','1 Receiver'),
            ('footer-distribution-old.pdf','footer-distribution-new.pdf','运行页脚（坐标证据）'),
        ]:
            with self.subTest(source=old):
                result=run_diff(FIXTURES/old,FIXTURES/new,DiffOptions())
                self.assertEqual(1,len(result.changes))
                change=result.changes[0]
                self.assertEqual(owner,change.old_section.heading)
                self.assertTrue(change.replaced_snippets or change.added_snippets or change.removed_snippets)
                self.assertNotEqual(change.old_section.body,change.new_section.body)

    def test_column_heading_source_spans_keep_independent_owners(self):
        sections=section_document(extract_pdf_text(FIXTURES/'two-column-owners.pdf'))
        technical={s.heading for s in sections if s.role=='technical'}
        self.assertTrue({'1 Reconstruction path','2 Operating limits','3 Continuity notes',
                         '4 Validation boundary'}.issubset(technical))
        self.assertFalse(any(s.number_path==('104.7',) for s in sections))

    def test_catalog_matching_uses_titles_and_occurrence_counts(self):
        from protocol_pdf_diff.catalog_evidence import catalog_identity_similarity
        from protocol_pdf_diff.compare import compare_sections
        from protocol_pdf_diff.models import Section
        def section(body):
            return Section('catalog', '0.5 List of Figures', 'List of Figures', 2,
                           ('List of Figures',), ('0.5',), 1, 2, body)
        titles = ['Receiver -3 dB', 'Clock 0.5 UI', 'Channel 112G', 'Calibration']
        old = section('\n'.join(f'Figure 1-{i} {title} .... {i+10}' for i,title in enumerate(titles)))
        new = section('\n'.join(f'Figure 1-{i} {title} ................. {i+20}' for i,title in enumerate(titles)) + '\nFigure 1-4 New setup ..... 30')
        self.assertGreater(catalog_identity_similarity(old,new), .72)
        changes = compare_sections([old],[new],DiffOptions())
        self.assertTrue(changes)
        self.assertTrue(all(c.old_section and c.new_section for c in changes))
        unrelated = section('\n'.join(f'Figure 1-{i} {title} .... {i+10}' for i,title in enumerate(['Budget forecast','Office locations','Staff roster','Vacation calendar'])))
        self.assertEqual(0, catalog_identity_similarity(old,unrelated))
        duplicate = section(old.body + '\nFigure 1-0 Receiver -3 dB .... 21')
        self.assertLess(catalog_identity_similarity(duplicate,old), 1)
        changed_value = section(old.body.replace('-3 dB', '-6 dB'))
        self.assertLess(catalog_identity_similarity(old,changed_value), 1)

    def test_footer_is_audited_separately_and_near_bottom_requirement_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'footer-owner.pdf'
            with fitz.open() as doc:
                p=doc.new_page(width=612,height=792)
                p.insert_text((60,70),'1 Receiver',fontname='hebo',fontsize=14)
                p.insert_text((60,110),'The receiver operates under the following specification.',fontsize=11)
                p.insert_text((40,735),'The standard shall preserve the operating voltage at 12',fontsize=11)
                p.insert_text((40,775),'Engineering Forum - Clause 1: Receiver Specification Revision A',fontsize=10)
                p.insert_text((550,775),'31',fontsize=10)
                doc.save(target)
            extraction=extract_pdf_text(target)
            page=extraction.pages[0]
            self.assertNotIn('Engineering Forum',page.text)
            self.assertIn('shall preserve',page.text)
            self.assertIn('Revision A',page.running_footer_texts[0])
            from protocol_pdf_diff.models import snapshot_page_extraction_audit
            self.assertEqual(page.running_footer_texts,snapshot_page_extraction_audit(extraction)[0].running_footer_texts)
            from protocol_pdf_diff.compare import _running_footer_section
            footer=_running_footer_section(extraction)
            self.assertIn('Revision A',footer.body)
            self.assertNotIn('31',footer.body)
            self.assertEqual('document_metadata',footer.role)

    def test_fractional_font_metrics_do_not_split_a_continuous_paragraph(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'metric-overlap.pdf'
            with fitz.open() as doc:
                p=doc.new_page()
                p.insert_text((72,70),'7 Receiver',fontname='hebo',fontsize=16)
                p.insert_text((72,110),'The receiver shall meet the limits defined by the referenced standard',fontsize=12.0192)
                p.insert_text((72,122),'802.3 [27] as modified by later revisions of the referenced document.',fontsize=12.0192)
                p.insert_text((72,170),'8 Transmitter',fontname='hebo',fontsize=16)
                doc.save(target)
            sections=section_document(extract_pdf_text(target))
            self.assertNotIn(('802.3',),[s.number_path for s in sections])
            self.assertIn('802.3 [27]',next(s for s in sections if s.number_path==('7',)).body)

    def test_separated_bottom_folio_cannot_own_a_table_or_start_a_chapter(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'separate-folio.pdf'
            with fitz.open() as doc:
                p=doc.new_page(width=612,height=792)
                p.insert_text((72,72),'1 Requirements',fontname='hebo',fontsize=14)
                p.insert_text((72,100),'The operating limits remain within the stated conditions.',fontsize=11)
                p.insert_text((45,774),'64',fontsize=11)
                p.insert_text((390,774),'Measurement User Guide',fontsize=11)
                doc.save(target)
            sections=section_document(extract_pdf_text(target))
            self.assertNotIn(('64',),[s.number_path for s in sections])

    def test_independent_titles_after_caption_and_short_technical_titles_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'region-owners.pdf'
            with fitz.open() as doc:
                p=doc.new_page()
                p.insert_text((72,70),'1 I/O',fontname='hebo',fontsize=14)
                for i in range(5):
                    p.insert_text((72,100+i*16),'The source contains technical requirements for all declared operating conditions.',fontsize=12)
                p.insert_text((72,220),'Figure 1. Setup',fontname='hebo',fontsize=12)
                p.insert_text((72,238),'2 Requirements',fontname='hebo',fontsize=12)
                p.insert_text((72,270),'The following requirement is independently numbered.',fontsize=12)
                doc.save(target)
            sections=section_document(extract_pdf_text(target))
            self.assertIn(('1',),[s.number_path for s in sections])
            self.assertIn(('2',),[s.number_path for s in sections])

    def test_owned_page_caches_close_on_success_and_interrupted_extraction(self):
        closed=[]
        original=EvidencePage.close
        def close(page):
            closed.append(page.page_number)
            return original(page)
        with patch.object(EvidencePage,'close',close):
            extract_pdf_text(FIXTURES/'empty.pdf')
        self.assertEqual([1],closed)
        closed.clear()
        with patch.object(EvidencePage,'close',close), patch(
                'protocol_pdf_diff.pdf_extract._extract_pdfplumber_page_text',
                side_effect=RuntimeError('directed extraction interruption')):
            with self.assertRaisesRegex(RuntimeError,'directed extraction interruption'):
                extract_pdf_text(FIXTURES/'empty.pdf')
        self.assertEqual([1],closed)

    def test_actual_empty_glyph_becomes_space_and_visible_private_glyph_survives(self):
        blank = extract_pdf_text(FIXTURES / 'empty.pdf').pages[0]
        visible = extract_pdf_text(FIXTURES / 'visible.pdf').pages[0]
        self.assertEqual('LEFT RIGHT', blank.text)
        self.assertEqual('LEFT\uf021RIGHT', visible.text)
        self.assertEqual(1, len(blank.source_blank_glyphs))
        bbox, original, digest, cid, gid = blank.source_blank_glyphs[0]
        self.assertEqual('\uf020', original)
        self.assertEqual(64, len(digest))
        self.assertEqual(cid, gid)
        self.assertFalse(visible.source_blank_glyphs)
        # Independent renderer verifies this real glyph's interior is blank.
        with fitz.open(FIXTURES / 'empty.pdf') as doc:
            rect = fitz.Rect(bbox) + (0.3, 0.3, -0.3, -0.3)
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(3,3), clip=rect, colorspace=fitz.csGRAY)
            self.assertTrue(all(value == 255 for value in pix.samples))

    def test_real_cid_mapping_cannot_be_replaced_by_unicode_or_font_name(self):
        # Same raw Unicode and same font: map the blank CID to a visible GID.
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'mapped.pdf'
            with fitz.open(FIXTURES / 'empty.pdf') as doc:
                xref = doc[0].get_fonts()[0][0]
                child = int(doc.xref_get_key(xref, 'DescendantFonts')[1].strip('[]').split()[0])
                stream = doc.get_new_xref(); doc.update_object(stream, '<<>>')
                mapping = bytearray().join(i.to_bytes(2,'big') for i in range(98))
                mapping[192:194] = (97).to_bytes(2, 'big')
                doc.update_stream(stream, bytes(mapping))
                doc.xref_set_key(child, 'CIDToGIDMap', f'{stream} 0 R')
                doc.save(target)
            page = extract_pdf_text(target).pages[0]
            self.assertEqual('LEFT\uf020RIGHT', page.text)
            self.assertFalse(page.source_blank_glyphs)

    def test_broken_font_and_missing_glyph_never_authorize_whitespace(self):
        data = (FIXTURES/'owned-glyph-evidence.ttf').read_bytes()
        self.assertTrue(empty_truetype_glyphs(data))
        self.assertFalse(empty_truetype_glyphs(data[:40]))
        self.assertIsNone(EmptyGlyphFont('a'*64, frozenset({0,2}), b'\x00').glyph(2))
        self.assertIsNone(EmptyGlyphFont('a'*64, frozenset({0}), None).glyph(0))

    def test_heading_source_binding_preserves_reference_and_recovers_real_chapter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'headings.pdf'
            with fitz.open() as doc:
                p=doc.new_page()
                p.insert_text((72,70),'7.2.3 Previous subsection',fontname='hebo',fontsize=12)
                p.insert_text((72,100),'The procedure uses the method described in',fontsize=12)
                p.insert_text((72,114),'Appendix 8.D. Measured values remain available',fontsize=12)
                p.insert_text((72,128),'for every declared test condition.',fontsize=12)
                p.insert_text((72,170),'4.175 - 0.0375',fontname='tiro',fontsize=12)
                p.insert_text((72,215),'9 Independent chapter',fontname='hebo',fontsize=16)
                for i in range(5): p.insert_text((72,245+i*16),'The following requirement preserves source identity and all actual limits.',fontsize=12)
                doc.save(path)
            sections=section_document(extract_pdf_text(path))
            self.assertFalse(any(s.number_path and s.number_path[-1].startswith('Appendix') for s in sections))
            self.assertFalse(any('4.175' in s.number_path for s in sections))
            self.assertTrue(any(s.number_path == ('9',) for s in sections))
            self.assertIn('Appendix 8.D.', '\n'.join(s.body for s in sections))

    def test_source_crop_stays_outside_a_same_height_table(self):
        crops=_crop_regions(page_bbox=(0,0,612,792), boxes=((100,100,150,120),),
                            blocking_bboxes=((200,90,500,140),),noise_bboxes=())
        self.assertTrue(crops)
        self.assertTrue(all(c[2] < 200 for c in crops))
        self.assertTrue(any(c[0] <= 100 and c[2] >= 150 for c in crops))

    def test_real_addition_is_automatic_but_incomplete_counterpart_is_neutral(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            old=write_multipage_text_pdf(root/'old.pdf', [['1 Start','The common start is stable.','3 End','The common end is stable.']])
            new=write_multipage_text_pdf(root/'new.pdf', [['1 Start','The common start is stable.','2 New limit','The calibrated limit shall be -3.0 V.','3 End','The common end is stable.']])
            result=run_diff(old,new,DiffOptions())
            additions=[c for c in result.changes if c.change_type=='added']
            self.assertEqual(1,len(additions))
            self.assertIn('-3.0 V',' '.join(additions[0].added_snippets))
            a,b=extract_pdf_text(old),extract_pdf_text(new)
            a=replace(a,pages=[replace(a.pages[0],ocr_used=True)])
            uncertain=compare_extractions(a,b,DiffOptions())
            change=next(c for c in uncertain.changes if c.new_section and c.new_section.title=='New limit')
            self.assertEqual('review',change.change_type)
            self.assertTrue(change.review_reason)
            self.assertTrue(all(not h.changed_token_indexes for h in _change_highlights(change,side='new')))
            report=write_reports(uncertain,root/'report',DiffOptions())['html'].read_text()
            self.assertIn('新版待核实原文',report)
            self.assertIn('-3.0 V',report)

    def test_duplicate_child_occurrence_is_not_consumed_twice(self):
        one='The calibrated transmitter shall preserve the complete operating voltage range.'
        two='The receiver shall retain the stated timing limits under every declared condition.'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            a=write_multipage_text_pdf(root/'old.pdf',[['1 Operation','Stable introduction.','1.1 First mode',one,two,'1.2 Second mode',one,two,'2 End','Final common requirement.']])
            b=write_multipage_text_pdf(root/'new.pdf',[['1 Operation','Stable introduction.',one,two,'2 End','Final common requirement.']])
            result=run_diff(a,b,DiffOptions())
            one_sided=[c for c in result.changes if c.old_section and not c.new_section]
            self.assertEqual(1,len(one_sided))
            self.assertIn(one,one_sided[0].old_section.body)

if __name__ == '__main__':
    unittest.main()
