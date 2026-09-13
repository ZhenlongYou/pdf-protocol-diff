"""Hand-authored conservation checks; no production imports or matching logic."""
import json
import sys


def check(case, observed):
    if 'equals' in case:
        assert observed == case['equals'], (case['id'], observed)
    for value in case.get('contains', []):
        assert value.casefold() in observed.casefold(), (case['id'], value, observed[:1200])
    for value in case.get('excludes', []):
        assert value.casefold() not in observed.casefold(), (case['id'], value)


def main():
    for path in sys.argv[1:]:
        for case in json.load(open(path))['cases']:
            good = case.get('equals', ' '.join(case.get('contains', [])))
            check(case, good)
            if good:
                try:
                    check(case, '')
                except AssertionError:
                    pass
                else:
                    raise AssertionError('empty output accepted')
    print('CONTENT_CORRESPONDENCE_ORACLE_OK')


if __name__ == '__main__':
    main()
