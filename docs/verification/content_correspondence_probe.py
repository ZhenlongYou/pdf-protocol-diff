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


def draw(path, maximum):
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
    doc.save(path)
    doc.close()


def run(case, root):
    kind = case['kind']
    if kind == 'limit':
        return reporting._table_row_value_display(case['value'])
    if kind == 'formula':
        return ' '.join(compare._paragraph_review_units(case['value'], suppressed_table_unit_keys=set()))
    if kind == 'fragment':
        return strip_coordinate_owned_visual_fragment(case['value'], tuple(case['sources']))
    root.mkdir(parents=True, exist_ok=True)
    old, new = root / 'old.pdf', root / 'new.pdf'
    draw(old, '1000'); draw(new, '1200')
    options = DiffOptions(visual_watchdog=False)
    result = compare.run_diff(old, new, options)
    paths = reporting.write_reports(result, root / 'report', options)
    payload = json.loads(paths['json'].read_text())
    return json.dumps(payload['content_table_changes'] + payload['similarity_review_table_changes'], ensure_ascii=False)


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
