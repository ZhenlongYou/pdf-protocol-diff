"""Regressions discovered by rerunning the complete original comparison."""
from types import SimpleNamespace
import unittest
from protocol_pdf_diff import compare, pdf_extract as extract, sectioning, reporting
from protocol_pdf_diff.models import TableVisual


class CompleteReportFollowupTests(unittest.TestCase):
    def test_publication_header_is_metadata_but_identifier_is_not(self):
        self.assertTrue(compare._publication_version_header('Implementation Agreement ABC-PHY-05.3 Common Electrical I/O'))
        self.assertFalse(compare._publication_version_header('Implementation Agreement Protocol ALPHA'))
        self.assertFalse(compare._publication_version_header('Implementation Agreement ABC-05.3 Mode FAST shall apply'))

    def test_reference_comma_is_not_heading(self):
        self.assertIsNone(sectioning.detect_heading('2.E.4.1, omitting any requirements relating to relative wander'))
        self.assertIsNotNone(sectioning.detect_heading('2.E.4.1 Relative wander'))

    def test_ordinary_spaced_folio_needs_cross_page_progression(self):
        page = SimpleNamespace(width=600, height=800)
        def words(number):
            labels = f'{number} Instrument Standards Forum - Clause 14: Medium Reach Interface'.split()
            return [dict(text=text,x0=70+i*40,x1=108+i*40,top=750,bottom=760) for i,text in enumerate(labels)]
        pages=[(i,page) for i in (312,314,316)]
        observed={i:words(i) for i,_ in pages}
        proof=extract._document_running_footer_evidence(pages,observed)
        self.assertEqual(set(proof),{312,314,316})
        self.assertNotIn('312',proof[312][2][0])
        self.assertEqual(extract._document_running_footer_evidence(pages[:2],observed),{})
        self.assertEqual(extract._document_running_footer_evidence(pages,{i:words(312) for i,_ in pages}),{})
        for row in observed.values(): row[1]['text']='shall'
        self.assertEqual(extract._document_running_footer_evidence(pages,observed),{})

    def test_multiline_definition_is_one_physical_record(self):
        row=[['Jitter','Generation'],['The output jitter is generated','without input jitter.']]
        expanded=[['Jitter ↵ Generation','The output jitter is generated ↵ without input jitter.']]
        words=[[dict(text=' '.join(cell),x0=i*100,x1=i*100+90,top=20,bottom=40)] for i,cell in enumerate(row)]
        self.assertTrue(extract._expanded_rows_preserve_source_alignment(row,expanded,cell_word_row=words,header=['Parameter','Description']))
        self.assertFalse(extract._expanded_rows_preserve_source_alignment(row,expanded,cell_word_row=words,header=['Parameter','Setting']))
        self.assertFalse(extract._expanded_rows_preserve_source_alignment(row,expanded,header=['Parameter','Description']))

    def test_setting_rows_use_parameter_identity_and_preserve_order_fields(self):
        rows=['Parameter=gamma | Setting=[0 1 2] | Units=', 'Parameter=C_d | Setting=4 | Units=nF']
        self.assertTrue(reporting._table_group_has_order_independent_parameter_identity((),(),rows,rows[::-1]))
        self.assertFalse(reporting._table_group_has_order_independent_parameter_identity((),(),['Parameter=A | Setting=1 | Order=1'],['Parameter=A | Setting=2 | Order=1']))

    def test_formula_number_does_not_erase_scalar_limit_or_following_prose(self):
        limit = '• RL(f) >= 12 dB for fmin < f <= fb/4'
        self.assertEqual(([], [], []), compare._summarize_text_delta(limit, limit+' (11-8)', 20)[:3])
        self.assertIn(limit, compare._paragraph_review_units(limit+' (11-8)', suppressed_table_unit_keys=set()))
        self.assertTrue(compare._summarize_text_delta(limit, limit.replace('12 dB','13 dB')+' (11-8)',20)[2])
        equation = 'VEC = -20 log(1 - 10**(-COM/20)) (25-19) '
        prose = 'This allows designers to choose equalization while meeting BER specifications.'
        self.assertIn(prose, compare._paragraph_review_units(equation+prose,suppressed_table_unit_keys=set()))
        self.assertEqual(([],[],[]),compare._summarize_text_delta(prose,equation+prose,20)[:3])
        self.assertTrue(compare._summarize_text_delta(equation+'The receiver shall meet the limit.',equation+'The receiver should meet the limit.',20)[2])

    def test_type0_embedded_truetype_needs_explicit_identity_mapping(self):
        from pathlib import Path
        import tempfile
        import fitz
        fixtures=Path(__file__).parent/'fixtures/source_evidence'
        with tempfile.TemporaryDirectory() as directory:
            for filename, mapping, expected in [('empty.pdf','/Identity','LEFT RIGHT'),
                                               ('visible.pdf','/Identity','LEFT\uf021RIGHT'),
                                               ('empty.pdf','null','LEFT\uf020RIGHT')]:
                target=Path(directory)/'font.pdf'
                with fitz.open(fixtures/filename) as doc:
                    parent=doc[0].get_fonts()[0][0]
                    child=int(doc.xref_get_key(parent,'DescendantFonts')[1].strip('[]').split()[0])
                    doc.xref_set_key(child,'Subtype','/CIDFontType0')
                    doc.xref_set_key(child,'CIDToGIDMap',mapping)
                    doc.save(target)
                self.assertEqual(expected,extract.extract_pdf_text(target).pages[0].text)
                target.unlink()

    def test_exact_overpaint_is_not_a_second_text_occurrence(self):
        from protocol_pdf_diff.exact_glyph_view import exact_glyph_comparison_view
        class Page:
            def __init__(self, chars): self.chars=chars
            def filter(self, predicate): return Page([c for c in self.chars if predicate(c)])
        char=dict(text='1',x0=10.,x1=20.,top=10.,bottom=20.,fontname='Font',size=10.,
                  stroking_color=(0,),non_stroking_color=(0,),matrix=(1,0,0,1,10,10),upright=True,adv=10.,ncs='DeviceGray',mcid=None,tag=None)
        self.assertEqual(1,len(exact_glyph_comparison_view(Page([char,dict(char)])).chars))
        variants=[dict(char,x0=10.+1e-9),dict(char,fontname='Other'),dict(char,size=11.),
                  dict(char,non_stroking_color=(1,)),dict(char,text='2'),dict(char,x1=21.),
                  dict(char,matrix=(1,0,0,1,20,10)),{k:v for k,v in char.items() if k!='matrix'}]
        for other in variants:
            self.assertEqual(2,len(exact_glyph_comparison_view(Page([char,other])).chars))
        self.assertEqual(2,len(exact_glyph_comparison_view(Page([char,char])).chars))

    def test_raised_marker_keeps_heading_source_identity(self):
        from protocol_pdf_diff.models import PageText, DocumentBlock, DocumentBlockKind
        from protocol_pdf_diff.heading_evidence import line_word_evidence
        title='8.B Appendix Template'
        words=(('8.B',70.,80.,90.,94.),('Appendix',110.,80.,180.,94.),('Template',185.,80.,250.,94.))
        marker=('1',250.,76.,256.,86.)
        def block(text, words, styles, order):
            return DocumentBlock(1,(70.,76.,256.,94.),DocumentBlockKind.TEXT,text,order,'test',
                                 word_boxes=words,word_styles=styles,font_names=('Bold',))
        page=PageText(1,'1\n'+title,blocks=(block('1',(marker,),(('Bold',10.5),),0),
                     block(title,words,(('Bold',14.),)*3,1)))
        self.assertEqual(['',title+'1'],sectioning._attach_raised_heading_markers(page,page.text.splitlines()))
        self.assertEqual(4,len(line_word_evidence(page,title+'1')))
        # A normal-sized number retains its original paragraph occurrence.
        from dataclasses import replace
        plain=replace(page,blocks=(replace(page.blocks[0],word_styles=(('Bold',14.),)),page.blocks[1]))
        self.assertEqual(['1',title],sectioning._attach_raised_heading_markers(plain,plain.text.splitlines()))

    def test_short_scalar_equation_number_does_not_change_content_survival(self):
        for text in ('0 <= Voltage <= 800 mV', 'Voltage = 800 mV + 5%'):
            self.assertIn(text, compare._paragraph_review_units(text+' (1-1)',suppressed_table_unit_keys=set()))
            self.assertEqual(([],[],[]),compare._summarize_text_delta(text,text+' (1-1)',20)[:3])
            self.assertTrue(compare._summarize_text_delta(text,text.replace('800','900')+' (1-1)',20)[2])

    def test_description_header_does_not_prove_numeric_list_alignment(self):
        row=[['Voltage','Current'],[],['1 V','2 A']]
        expanded=[['Voltage ↵ Current','','1 V ↵ 2 A']]
        words=[[dict(text=' '.join(cell),x0=i*100,x1=i*100+90,top=20,bottom=40)] if cell else [] for i,cell in enumerate(row)]
        self.assertFalse(extract._expanded_rows_preserve_source_alignment(row,expanded,cell_word_row=words,header=['Parameter','Column 2','Description']))

    def test_negative_exponent_glyph_variants_keep_sign_and_value(self):
        from protocol_pdf_diff.content_equivalence import cosmetic_content_equal
        self.assertTrue(cosmetic_content_equal('Setting=6.141E‐03','Setting=6.141E-03'))
        self.assertFalse(cosmetic_content_equal('Setting=6.141E‐03','Setting=6.141E+03'))
        self.assertFalse(cosmetic_content_equal('Setting=6.141E‐03','Setting=6.141E-04'))

    def test_unique_exact_single_row_continuation_requires_complete_evidence(self):
        from dataclasses import replace
        row='Column 1=F31 | Column 2=3 to 1 fall | Column 3=033 1112 | Column 4=263'
        old=TableVisual(10,1,'',(70,70,540,100),'',[row],'',is_continuation=True,
                        content_fully_represented=True,row_alignment_reliable=True,data_rows_fully_represented=True)
        new=replace(old,page_number=14)
        groups=reporting._paired_table_visuals([old],[new])
        self.assertEqual([(1,1)],[(len(g.old_tables),len(g.new_tables)) for g in groups])
        for others in ([new,replace(new,page_number=15)],[replace(new,row_alignment_reliable=False)]):
            self.assertFalse(any(g.old_tables and g.new_tables for g in reporting._paired_table_visuals([old],others)))

    def test_figure_panel_classification_has_caption_and_schema_vetoes(self):
        page=SimpleNamespace(rects=[dict(x0=100,top=100,x1=400,bottom=300)])
        words=[dict(text='Figure 1. Diagram',x0=120,x1=280,top=70,bottom=85)]
        box=(120,120,300,200)
        self.assertTrue(extract._table_inside_captioned_drawing_panels(page,box,['Column 1=Clock | Column 2=Reference'],words))
        self.assertFalse(extract._table_inside_captioned_drawing_panels(page,box,['Parameter=Gain | Min=0 | Max=1 | Unit=dB'],words))
        self.assertFalse(extract._table_inside_captioned_drawing_panels(page,box,['Column 1=Clock | Column 2=Reference'],[]))

if __name__=='__main__': unittest.main()
