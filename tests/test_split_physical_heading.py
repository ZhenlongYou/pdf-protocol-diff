"""Preserve physical split heading identity and genuinely added child sections."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from protocol_pdf_diff import sectioning, compare
from protocol_pdf_diff.models import DocumentBlock, DocumentBlockKind, PageText, ExtractionResult, Section, DiffOptions

FIXTURE = Path(__file__).parent / 'fixtures/content-correspondence/split-physical-heading.json'

def physical_heading_page():
    data=json.loads(FIXTURE.read_text())
    blocks=tuple(DocumentBlock(data['page_number'],tuple(b['bbox']),DocumentBlockKind(b['kind']),
        b['text'],b['reading_order'],b['source_engine'],font_names=tuple(b['font_names']),
        word_boxes=tuple(tuple(w) for w in b['word_boxes']),word_styles=tuple(tuple(w) for w in b['word_styles'])) for b in data['blocks'])
    return PageText(data['page_number'],data['text'],blocks=blocks,page_bbox=tuple(data['page_bbox']),
        ambiguous_line_number_sides=tuple(data['ambiguous_line_number_sides']),
        visual_noise_bboxes=tuple(tuple(b) for b in data['visual_noise_bboxes']))

class SplitPhysicalHeadingTests(unittest.TestCase):
    def test_real_physical_split_heading_is_one_child_with_body(self):
        page=physical_heading_page()
        sections=sectioning.section_document(ExtractionResult(Path('fixture.pdf'),[page]))
        children=[s for s in sections if s.heading=='27.3.1.7.1 J3u and JRMS Jitter']
        self.assertEqual(1,len(children))
        self.assertIn('For each transition',children[0].body)
        self.assertNotIn('27.3.1.7.1',children[0].body)

    def test_source_split_heading_rejects_five_unproven_spans(self):
        page=physical_heading_page()
        raw='27.3.1.7.1 J\n3u\nand J\nRMS\nJitter'
        normal_blocks=tuple(replace(b,word_styles=tuple(('Arial',12.) for _ in b.word_styles)) for b in page.blocks)
        cases={
            'changed_subscript_without_source':replace(page,text=page.text.replace(raw,raw.replace('3u','4u'))),
            'missing_source_word':replace(page,text=page.text.replace(raw,raw.replace('\nRMS',''))),
            'intervening_real_requirement':replace(page,text=page.text.replace(raw,raw.replace('\nand J','\nThe receiver shall retain this condition.\nand J'))),
            'ambiguous_duplicate_occurrence':replace(page,text=page.text+'\n'+raw),
            'ordinary_body_style':replace(page,blocks=normal_blocks),
        }
        for label,candidate in cases.items():
            with self.subTest(case=label):
                output=sectioning._merge_source_split_heading_lines([candidate])[0]
                self.assertEqual(candidate.text,output.text)

    def test_genuinely_added_child_remains_added(self):
        page=physical_heading_page()
        child=next(s for s in sectioning.section_document(ExtractionResult(Path('fixture.pdf'),[page]))
                   if s.heading=='27.3.1.7.1 J3u and JRMS Jitter')
        heading='27.3.1.7 Transmitter output jitter'
        parent=Section('parent',heading,'Transmitter output jitter',4,(heading,),('27.3.1.7',),630,630,
                       'The transmitter shall satisfy its specified amplitude limit.')
        changes=compare.compare_sections([parent],[parent,child],DiffOptions())
        additions=[c for c in changes if c.change_type=='added' and c.new_section and c.new_section.section_id==child.section_id]
        self.assertEqual(1,len(additions))
        self.assertTrue(any('For each transition' in value for value in additions[0].added_snippets))

if __name__=='__main__':unittest.main()
