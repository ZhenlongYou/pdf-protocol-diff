"""小块截图 OCR 独立于正文；图示区域守卫拒绝有文字或图题歧义的输入。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from protocol_pdf_diff.image_regions import extract_image_region_text, captioned_graphic_regions, image_region_sections
from protocol_pdf_diff.models import PageText, ExtractionResult, DiffOptions, Section, DocumentBlock, DocumentBlockKind
from protocol_pdf_diff.compare import compare_extractions, _section_with_descendant_body
from pathlib import Path
from dataclasses import replace
from protocol_pdf_diff.prose_source_visuals import _source_context_region


class RegionPage:
    bbox=(0,0,612,792)
    width=612; height=792; rotation=0
    images=[dict(x0=80,top=200,x1=400,bottom=300)]
    chars=[dict(text='Body',x0=80,top=90,x1=110,bottom=102)]
    def crop(self,box):
        return SimpleNamespace(bbox=box,to_image=lambda **kw: SimpleNamespace(original=Image.new('RGB',(200,80),'white')))


class ImageRegionsTests(unittest.TestCase):
    def test_merged_child_context_is_not_cropped_at_child_heading(self):
        parent=Section('1','1 Scope','Scope',1,('1 Scope',),('1',),1,1,'Parent condition.')
        child=Section('1.1','1.1 Limits','Limits',2,('1 Scope','1.1 Limits'),('1','1'),1,1,'Child limit is 3.3 V.')
        merged=_section_with_descendant_body(parent,child)
        blocks=tuple(DocumentBlock(1,box,DocumentBlockKind.TEXT,text,i,'pdfplumber')
                     for i,(box,text) in enumerate((
                         ((50,50,200,65),'1 Scope'),
                         ((50,90,300,105),'Parent condition.'),
                         ((50,150,200,165),'1.1 Limits'),
                         ((50,190,300,205),'Child limit is 3.3 V.'))))
        page=PageText(1,'\n'.join(b.text for b in blocks),blocks=blocks,page_bbox=(0,0,612,792))
        self.assertIn(child.body,dict(merged.page_bodies)[1])
        self.assertEqual((page.page_bbox,'source-page'),_source_context_region(page,merged,1))

    def test_duplicate_ocr_text_uses_explicit_region_identity(self):
        with patch('protocol_pdf_diff.image_regions.cached_image_to_string',return_value='Threshold shall be 3.3 V.'):
            regions,_=extract_image_region_text(RegionPage(),1)
        first=regions[0];second=replace(first,bbox=(80,400,400,500))
        page=PageText(1,'1 Scope',page_bbox=(0,0,612,792),image_text_regions=(first,second))
        sections=image_region_sections(ExtractionResult(Path('regions.pdf'),[page]))
        for s,b in zip(sections,(first,second)):
            self.assertEqual((b.bbox,'source-image-region'),_source_context_region(page,s,1))
        wrong=replace(sections[0],body='Different OCR content.')
        self.assertEqual((page.page_bbox,'source-page'),_source_context_region(page,wrong,1))

    def test_partial_image_has_own_bbox_and_does_not_enter_native_clause(self):
        with patch('protocol_pdf_diff.image_regions.cached_image_to_string',return_value='Call ENABLE\nCall enable'):
            regions,warnings=extract_image_region_text(RegionPage(),1)
        self.assertEqual((80,200,400,300),regions[0].bbox)
        self.assertEqual('Call ENABLE\nCall enable',regions[0].text)
        self.assertTrue(warnings)
        page=PageText(1,'1 Scope\nThe receiver shall remain enabled.',image_text_regions=regions)
        new=PageText(1,page.text)
        result=compare_extractions(ExtractionResult(Path('a.pdf'),[page],warnings), ExtractionResult(Path('b.pdf'),[new]), DiffOptions())
        self.assertEqual(page.text,'1 Scope\nThe receiver shall remain enabled.')
        self.assertTrue(any(c.change_type=='review' and c.old_section and 'Call enable' in c.old_section.body for c in result.changes))
        self.assertFalse(result.assessment.allows_no_difference_conclusion)

    def test_native_text_or_table_overlap_prevents_duplicate_recognition(self):
        for excluded in (((90,210,100,250),),):
            with patch('protocol_pdf_diff.image_regions.cached_image_to_string') as recognize:
                regions,_=extract_image_region_text(RegionPage(),1,excluded)
                self.assertFalse(regions);recognize.assert_not_called()
        page=RegionPage();page.chars=[dict(text='hidden layer',x0=90,top=210,x1=180,bottom=230)]
        with patch('protocol_pdf_diff.image_regions.cached_image_to_string') as recognize:
            self.assertFalse(extract_image_region_text(page,1)[0]);recognize.assert_not_called()

    def test_caption_and_complete_native_geometry_are_required(self):
        words=[dict(text='Figure 1. Receiver path',x0=100,top=315,x1=350,bottom=330)]
        page=RegionPage()
        self.assertEqual((('raster','Figure 1. Receiver path',(80,200,400,300)),),captioned_graphic_regions(page,words,()))
        page.chars=[dict(text='Label',x0=90,top=210,x1=140,bottom=222)]
        self.assertFalse(captioned_graphic_regions(page,words,()))
        page.chars=[dict(text='broken')]
        self.assertFalse(captioned_graphic_regions(page,words,()))
