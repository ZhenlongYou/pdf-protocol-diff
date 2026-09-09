"""Expected inventories from the manually specified scene, no production imports."""
import json
import sys


def check(case, observed):
    assert observed['table_count']==case['expected_tables'],observed
    assert any('Frequency (GHz)' in s and '40' in s for s in observed['real_rows']),observed
    assert observed['has_images'] and observed['full_table_pages'],observed
    assert observed['changes']>0 and '10 mV' in observed['body'],observed
    if case['expected_tables']==1:
        assert all(case.get('label','Horizontal (unit)') not in s for s in observed['table_payloads']),observed
    else:
        assert any(case.get('label','Horizontal (unit)') in s for s in observed['table_payloads']),observed
        if case.get('aux'):assert any(case['aux'] in s for s in observed['table_payloads']),observed


def main():
    for file in sys.argv[1:]:
        for case in json.load(open(file))['cases']:
            good=dict(table_count=case['expected_tables'],real_rows=['Frequency (GHz) | 40'],has_images=True,full_table_pages=True,changes=1,body='10 mV',table_payloads=[case.get('label','Horizontal (unit)')] if case['expected_tables']>1 else [])
            if case.get('aux'):good['table_payloads'].append(case['aux'])
            check(case,good)
            for key in ('table_count','has_images','changes','real_rows'):
                bad=dict(good);bad[key]=None
                try:check(case,bad)
                except (AssertionError,TypeError):pass
                else:raise AssertionError('oracle accepted broken '+key)
    print('PLOT_AXIS_ORACLE_OK')
if __name__=='__main__':main()
