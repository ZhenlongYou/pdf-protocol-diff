"""Exercise public extraction/comparison/report paths with frozen case inputs."""
import json
from pathlib import Path
import sys
import tempfile
import argparse

ROOT = Path.cwd()  # Test the checkout under execution, including immutable RED archives.
sys.path.insert(0, str(ROOT / 'src'))
from protocol_pdf_diff.compare import compare_extractions, run_diff
from protocol_pdf_diff.models import DiffOptions, DiffResult, ExtractionResult, PageText, TableVisual
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.table_codec import encode_table_field
from content_only_oracle import assert_report


def make_pdf(path, pages):
    # FitZ draws hand-authored source lines; no production fixture/export helper.
    import fitz
    document = fitz.open()
    for lines in pages:
        page = document.new_page()
        for index, line in enumerate(lines):
            page.insert_text((50, 65 + index * 26), line, fontsize=13 if index == 0 else 11)
    document.save(path)
    document.close()


def run_case(case, root):
    options = DiffOptions(visual_watchdog=False)
    if case['kind'] == 'invalid':
        try:
            DiffOptions(min_section_match_similarity=float(case['threshold']))
        except ValueError:
            return
        raise AssertionError(case['id'] + ': invalid threshold accepted')
    if case['kind'] == 'pdf':
        old, new = root/'old.pdf', root/'new.pdf'
        make_pdf(old, case['old_pages']); make_pdf(new, case['new_pages'])
        result = run_diff(old, new, options)
    elif case['kind'] == 'pages':
        result = compare_extractions(
            ExtractionResult(root/'old.pdf', [PageText(i, '\n'.join(lines)) for i, lines in enumerate(case['old_pages'], 1)]),
            ExtractionResult(root/'new.pdf', [PageText(i, '\n'.join(lines)) for i, lines in enumerate(case['new_pages'], 1)]), options)
    elif case['kind'] == 'prose':
        result = compare_extractions(
            ExtractionResult(root/'old.pdf', [PageText(1, case['old'])]),
            ExtractionResult(root/'new.pdf', [PageText(1, case['new'])]), options)
    else:
        def table(value):
            rows = ['表格行: T1 | Parameter='+case.get('item', 'Limit')+' | '+encode_table_field('Value' if case.get('item') else 'Description', value),
                    '表格行: T1 | Parameter=Other | Value=2']
            return TableVisual(1, 1, case.get('caption', 'Table 1. Limits'), (0,0,100,100), '', rows, 'grid',
                               content_fully_represented=True, row_alignment_reliable=case['reliable'])
        result = DiffResult(root/'old.pdf', root/'new.pdf', [], [], [], [],
                            old_table_visuals=[table(case['old'])], new_table_visuals=[table(case['new'])])
    outputs = write_reports(result, root/'reports', options)
    md = outputs['markdown'].read_text()
    csv_text = outputs['csv'].read_text(encoding='utf-8-sig') + outputs['table_csv'].read_text(encoding='utf-8-sig')
    assert_report(case, md, csv_text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('fixtures', nargs='+')
    parser.add_argument('--artifact')
    args = parser.parse_args()
    count = 0
    failed = []
    for filename in args.fixtures:
        payload = json.loads(Path(filename).read_text())
        for case in payload['cases']:
            with tempfile.TemporaryDirectory(prefix='content-case-') as directory:
                try:
                    run_case(case, Path(directory))
                except AssertionError as error:
                    failed.append(str(error))
            count += 1
    print('CONTENT_ONLY_TEST')
    if failed:
        print('CONTENT_CONTRACT_FAIL: ' + '; '.join(failed))
        return 1
    print(f'CONTENT_CONTRACT_OK cases={count}')
    if args.artifact:
        artifact = Path(args.artifact)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps({'validated_cases': count, 'contract': 'substantive_content', 'failures': failed})+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
