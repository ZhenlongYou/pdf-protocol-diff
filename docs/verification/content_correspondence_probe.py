"""Run semantic conservation examples and a PDF-to-report public path."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import fitz
ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / 'src'))
from protocol_pdf_diff import compare, reporting
from protocol_pdf_diff.figure_filters import strip_coordinate_owned_visual_fragment
from protocol_pdf_diff.models import DiffOptions
from content_correspondence_oracle import check


def draw(path, maximum, phrase=None):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 40), '1 Receiver requirements', fontsize=14)
    page.insert_text((50, 70), 'The receiver shall meet the specified voltage limits.', fontsize=10)
    page.insert_text((50, 110), 'Table 1. Voltage limits', fontsize=10)
    for y in (125, 150, 175):
        page.draw_line((50, y), (500, y))
    for x in (50, 250, 330, 410, 500):
        page.draw_line((x, 125), (x, 175))
    for row, y in ((['Parameter', 'Min', 'Max', 'Unit'], 143), (['Swing', '100', maximum, 'mV'], 168)):
        for x, text in zip((55, 255, 335, 415), row):
            page.insert_text((x, y), text, fontsize=10)
    if phrase:
        page.insert_text((50, 205), 'Figure 2. Voltage monitor', fontsize=10)
        page.draw_rect((50, 215, 500, 290))
        page.insert_text((60, 245), 'Maximum differential voltage: 900 mV', fontsize=10)
        page.insert_text((50, 330), phrase, fontsize=10)
    doc.save(path)
    doc.close()


def run(case, root):
    kind = case['kind']
    if kind == 'owned':
        from protocol_pdf_diff.visual_ownership import apply_owned_spans
        return apply_owned_spans(case['value'], case['spans'])
    if kind == 'frame':
        from types import SimpleNamespace
        from protocol_pdf_diff.pdf_extract import _table_fragment_inside_figure_frame
        edges = [dict(orientation='h', x0=300, x1=550, top=y, bottom=y) for y in (100,250)]
        edges += [dict(orientation='v', x0=x, x1=x, top=100, bottom=250) for x in (300,550)]
        left = case['caption_left']
        words = [dict(text='Figure 1. Response', x0=left, x1=left+150, top=75, bottom=90)]
        return str(_table_fragment_inside_figure_frame(SimpleNamespace(edges=edges), (320,120,530,220),
            ['Column 1=Gain(In | Column 2=put)'], words))
    if kind == 'schema':
        old, new = reporting._remove_one_sided_leading_schema_rows([], case['rows'])
        return '\n'.join(new)
    if kind == 'header':
        from protocol_pdf_diff.pdf_extract import _geometry_proven_grouped_header
        return json.dumps(_geometry_proven_grouped_header(case['rows'], 1, case['bounds']), ensure_ascii=False)
    if kind == 'group':
        from protocol_pdf_diff.models import TableVisual
        tables = [TableVisual(1, i+1, 'Table 1 Limits' if i == 0 else '',
            (0, i*25, 100, i*25+20), '', ['Parameter=A | Value=1'], '', is_continuation=i>0)
            for i in range(case['count'])]
        return json.dumps(list(reporting._table_visual_indexes_by_caption_key(tables).values()))
    if kind == 'ownership':
        from protocol_pdf_diff.models import (DiffResult, DocumentBlock, DocumentBlockKind,
            ExtractionResult, PageText, Section, SectionChange, ProseSourceVisual, ProseSourceVisualGroup)
        from protocol_pdf_diff.visual_ownership import build_visual_owned_spans, apply_owned_spans
        blocks = []
        for index, line in enumerate(case['lines']):
            x, top, words = 10., float(line['top']), []
            for token in line['text'].split():
                words.append((token, x, top, x+4*len(token), top+8))
                x += 4*len(token)+4
            blocks.append(DocumentBlock(1, (10,top,x,top+8), DocumentBlockKind.TEXT,
                line['text'], index, 'native', word_boxes=() if line.get('missing_words') else tuple(words)))
        blocks.append(DocumentBlock(1, (10.,-20.,50.,-10.), DocumentBlockKind.TEXT, 'Figure 1.', -1, 'native', word_boxes=(('Figure',10.,-20.,34.,-10.),('1.',38.,-20.,46.,-10.))))
        page = PageText(1, '\n'.join(line['text'] for line in case['lines']), blocks=tuple(blocks), vector_graphic_bboxes=((0.,0.,500.,50.),))
        value = case['value']
        section = Section('new', '1 Requirements', 'Requirements', 1, ('1',), ('1',), 1, 1, value)
        change = SectionChange('added', None, section, 0., added_snippets=[value])
        result = DiffResult(Path('old.pdf'), Path('new.pdf'), [], [section], [change], [])
        visual = ProseSourceVisual(1, (0.,0.,500.,50.), '', 0, 0)
        groups = [ProseSourceVisualGroup('added', None, 'new', new_figure_visuals=(visual,), new_figure_captions=('Figure 1.',))]
        if case.get('uncertain_table'):
            from protocol_pdf_diff.models import TableVisual
            result.new_table_visuals.append(TableVisual(1,1,'Table 1', (0,0,500,50), '', [], '', content_fully_represented=False, row_alignment_reliable=False))
            groups = []
        spans = build_visual_owned_spans(result, ExtractionResult(Path('old.pdf'), []),
            ExtractionResult(Path('new.pdf'), [page]), groups)
        return apply_owned_spans(value, spans.get('new:new', {}).get(value, []))
    if kind == 'limit':
        return reporting._table_row_value_display(case['value'])
    if kind == 'formula':
        return ' '.join(compare._paragraph_review_units(case['value'], suppressed_table_unit_keys=set()))
    if kind == 'fragment':
        return strip_coordinate_owned_visual_fragment(case['value'], tuple(case['sources']))
    root.mkdir(parents=True, exist_ok=True)
    old, new = root / 'old.pdf', root / 'new.pdf'
    draw(old, '1000', 'Maximum differential voltage: 800 mV' if kind == 'public_visual' else None)
    draw(new, '1200', 'Maximum differential voltage: 900 mV' if kind == 'public_visual' else None)
    options = DiffOptions(visual_watchdog=False)
    result = compare.run_diff(old, new, options)
    paths = reporting.write_reports(result, root / 'report', options)
    payload = json.loads(paths['json'].read_text())
    facts = payload['content_table_changes'] + payload['similarity_review_table_changes']
    if kind == 'public_visual':
        facts += payload['content_changes'] + payload['similarity_review_changes']
    return json.dumps(facts, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('fixtures', nargs='+')
    parser.add_argument('--artifact')
    args = parser.parse_args()
    print('CONTENT_CORRESPONDENCE_TEST', flush=True)
    try:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(args.artifact).parent / 'public-reports' if args.artifact else Path(temp)
            rows = []
            for path in args.fixtures:
                for case in json.loads(Path(path).read_text())['cases']:
                    observed = run(case, root / case['id'])
                    check(case, observed)
                    rows.append(dict(id=case['id'], observed=observed))
            if args.artifact:
                Path(args.artifact).write_text(json.dumps(rows, indent=2))
    except AssertionError as error:
        print('CONTENT_CORRESPONDENCE_CONTRACT_FAIL', str(error)[:1500])
        return 1
    print('CONTENT_CORRESPONDENCE_CONTRACT_OK', len(rows))
    return 0


if __name__ == '__main__':
    sys.exit(main())
