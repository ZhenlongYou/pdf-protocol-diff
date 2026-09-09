"""Independent reader assertions; no production parser or equivalence imports."""
import json
import re
import sys
from pathlib import Path


def assert_report(case, markdown, csv_text):
    expected = case['expect']
    if expected.get('no_cards'):
        assert not re.search(r'^### ', markdown, re.M), f"{case['id']}: unwanted content card"
        assert len(csv_text.strip().splitlines()) <= 2, f"{case['id']}: CSV disagrees with content report"
    for value in expected.get('contains', []):
        assert value in markdown, f"{case['id']}: missing {value}"
    for value in expected.get('not_contains', []):
        assert value not in markdown, f"{case['id']}: unwanted {value}"
        assert value not in csv_text, f"{case['id']}: CSV retains {value}"
    if expected.get('table_cards'):
        assert re.search(r'^### T\d+\.', markdown, re.M), f"{case['id']}: lost table change"


def main():
    count = 0
    identifiers = set()
    for filename in sys.argv[1:]:
        data = json.loads(Path(filename).read_text())
        assert data['authority'] and data['cases']
        for case in data['cases']:
            assert case['id'] not in identifiers
            identifiers.add(case['id'])
            assert case['expect'] and case['kind'] in ('prose', 'pages', 'table', 'pdf', 'invalid')
            count += 1
    assert count > 0
    # Prove assertions reject both invented changes and loss of a required fact.
    for case, md in [({'id':'negative-noise','expect':{'no_cards':True}}, '### 1. Modified'),
                     ({'id':'negative-loss','expect':{'contains':['12 mV']}}, '10 mV')]:
        try:
            assert_report(case, md, '')
        except AssertionError:
            pass
        else:
            raise AssertionError('oracle negative control survived')
    print(f'CONTENT_ORACLE_OK cases={count}')


if __name__ == '__main__':
    main()
