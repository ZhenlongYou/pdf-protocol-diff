"""Independent word positions distinguish shared vocabulary from ownership."""
from pathlib import Path
import unittest
from protocol_pdf_diff.models import (DiffResult, DocumentBlock, DocumentBlockKind,
    ExtractionResult, PageText, Section, SectionChange, ProseSourceVisual, ProseSourceVisualGroup)
from protocol_pdf_diff.visual_ownership import build_visual_owned_spans, apply_owned_spans
from protocol_pdf_diff.figure_filters import strip_coordinate_owned_visual_fragment


def fixture(value, lines):
    blocks = []
    for index, (text, top) in enumerate(lines):
        x, words = 10., []
        for token in text.split():
            words.append((token, x, top, x + len(token)*4, top+8))
            x += len(token)*4 + 4
        blocks.append(DocumentBlock(1, (10,top,x,top+8), DocumentBlockKind.TEXT,
                                    text, index, 'native', word_boxes=tuple(words)))
    page = PageText(1, '\n'.join(text for text, _ in lines), blocks=tuple(blocks))
    section = Section('new', '1 Requirements', 'Requirements', 1, ('1',), ('1',), 1, 1,
                      value, page_bodies=((1,page.text),))
    change = SectionChange('added', None, section, 0., added_snippets=[value])
    result = DiffResult(Path('old.pdf'), Path('new.pdf'), [], [section], [change], [])
    visual = ProseSourceVisual(1, (0.,0.,500.,50.), '', 0, 0)
    groups = [ProseSourceVisualGroup('added', None, 'new', new_figure_visuals=(visual,))]
    spans = build_visual_owned_spans(result, ExtractionResult(Path('old.pdf'), []),
                                    ExtractionResult(Path('new.pdf'), [page]), groups)
    return spans.get('new:new', {}).get(value, [])


class OwnershipTests(unittest.TestCase):
    def test_repeated_same_page_phrase_is_not_unique(self):
        value = 'Maximum differential voltage: 800 mV'
        self.assertEqual([], fixture(value, [(value,10.), (value,100.)]))

    def test_unique_outside_figure_is_not_owned(self):
        value = 'Maximum differential voltage: 800 mV'
        self.assertEqual([], fixture(value, [(value,100.)]))

    def test_unique_inside_figure_is_owned(self):
        value = 'Gain versus frequency'
        spans = fixture(value, [(value,10.)])
        self.assertEqual([(0,len(value))], spans)
        self.assertEqual('', apply_owned_spans(value, spans))

    def test_prefix_ownership_keeps_negative_value(self):
        value = 'Alpha Beta – 10.75 dB'
        spans = fixture(value, [('Alpha Beta',10.), ('– 10.75 dB',100.)])
        self.assertEqual('– 10.75 dB', apply_owned_spans(value, spans))

    def test_missing_source_occurrence_keeps_all(self):
        value = 'The receiver shall preserve the limit.'
        self.assertEqual([], fixture(value, [('receiver The limit preserve shall the.',10.)]))

    def test_text_only_inventory_cannot_trim_requirements(self):
        for value, source in [
            ('Output differential voltage shall remain below 800 mV', 'Output differential voltage'),
            ('The receiver shall support this limit: 800 mV max', '800 mV max'),
            ('Alpha Beta Alpha Beta', 'Alpha Beta')]:
            self.assertEqual(value, strip_coordinate_owned_visual_fragment(value, (source,)))

    def test_invalid_interval_keeps_source(self):
        self.assertEqual('800 mV', apply_owned_spans('800 mV', [(0,100)]))

    def test_missing_words_in_second_occurrence_prevent_uniqueness(self):
        from dataclasses import replace
        from unittest.mock import patch
        # Corrupt only the second block; source text still records both copies.
        original = DocumentBlock
        def block(*args, **kwargs):
            if args[4] == 1:
                kwargs['word_boxes'] = ()
            return original(*args, **kwargs)
        value = 'Maximum differential voltage: 800 mV'
        with patch(__name__ + '.DocumentBlock', side_effect=block):
            self.assertEqual([], fixture(value, [(value,10.), (value,100.)]))

    def test_normative_sentence_inside_oversized_crop_is_retained(self):
        value = 'The limit shall remain 10 mV.'
        self.assertEqual([], fixture(value, [(value,10.)]))

    def test_unrepresented_table_does_not_own_requirement(self):
        from protocol_pdf_diff.models import TableVisual
        from unittest.mock import patch
        value = 'Maximum differential voltage: 800 mV'
        table = TableVisual(1,1,'Table 1', (0,0,500,50), '', [], '',
                            content_fully_represented=False, row_alignment_reliable=False)
        # The same positions receive no Figure certificate, only an uncertain table.
        original = build_visual_owned_spans
        def with_table(result, old, new, groups):
            result.old_table_visuals[:] = []
            result.new_table_visuals[:] = [table]
            return original(result, old, new, [])
        with patch(__name__ + '.build_visual_owned_spans', side_effect=with_table):
            self.assertEqual([], fixture(value, [(value,10.)]))


if __name__ == '__main__':
    unittest.main()
