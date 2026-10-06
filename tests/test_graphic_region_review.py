"""真实两份单页 PDF：正文变化不遮掉固定图示变化，也不把正文改字涂成图示变化。"""
import tempfile
import unittest
import hashlib
from pathlib import Path
from PIL import Image, ImageDraw
from protocol_pdf_diff.models import PageText, ExtractionResult, VisualWatchdogAudit
from protocol_pdf_diff.graphic_region_review import compare_captioned_graphics
from protocol_pdf_diff.visual_watchdog import _compare_page_images


class GraphicRegionReviewTests(unittest.TestCase):
    def test_region_mask_ignores_changes_outside_allowed_region(self):
        old=Image.new('RGB',(200,200),'white');new=old.copy();ImageDraw.Draw(new).rectangle((5,5,60,30),fill='black')
        kwargs=dict(old_page_number=1,new_page_number=2,alignment_method='test',old_page_bbox=(0,0,200,200),new_page_bbox=(0,0,200,200),allowed_bboxes=((50,100,150,160),))
        self.assertIsNone(_compare_page_images(old,new,**kwargs))
        ImageDraw.Draw(new).rectangle((80,110,100,130),fill='black')
        self.assertIsNotNone(_compare_page_images(old,new,**kwargs))

    def test_fixed_unique_caption_region_keeps_whole_page_incomplete(self):
        import fitz
        with tempfile.TemporaryDirectory() as d:
            docs=[]
            for side,text,color in [('old','Limit 3 V',(0,0,0)),('new','Limit 5 V',(1,0,0))]:
                path=Path(d)/(side+'.pdf')
                with fitz.open() as pdf:
                    p=pdf.new_page(width=300,height=400);p.insert_text((30,60),text)
                    p.draw_circle((130,200),30,color=color,fill=color);pdf.save(path)
                page=PageText(1,text,page_bbox=(0,0,300,400),graphic_regions=(('vector','Figure 1. Path',(70,140,190,260)),))
                docs.append(ExtractionResult(path,[page],total_pages=1,source_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            audit=VisualWatchdogAudit(True,False,None,0,0,0,0,0,False,None,semantic_change_pages=((1,None),(None,1)),semantic_change_page_count=2)
            items,warnings,updated=compare_captioned_graphics(*docs,audit)
            self.assertEqual(1,len(items));self.assertFalse(warnings)
            self.assertEqual(1,len(updated.checked_graphic_regions))
            self.assertFalse(updated.complete);self.assertEqual(0,updated.checked_page_pair_count)
            self.assertEqual(audit.semantic_change_pages,updated.semantic_change_pages)
            docs[1].pages[0]=PageText(1,'Limit 5 V',page_bbox=(0,0,300,400),graphic_regions=(('vector','Figure 1. Path',(71,140,191,260)),))
            self.assertFalse(compare_captioned_graphics(*docs,audit)[0])
