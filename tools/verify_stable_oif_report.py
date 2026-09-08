"""Audit the completed real OIF GUI run against fixed user counterexamples.

This validates existing GUI evidence and exact artifacts; it does not claim to
launch a new comparison or certify all content in either private source PDF.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

INPUTS = {
    'old': 'dc504341bd4190bfc98f32bf1bf5c683cc6cf5c22906f45a00a09b2870a90191',
    'new': '1fa2417c96f06bc8115bcc79b7d2f7a160e35b8cc5bbebe7051fd446833e2f72',
}
ANCHORS = (
    'The reference mated MCB-HCB loss is given in Equation (16-11).',
    'The channel consists of Host PCB trace, Module PCB trace, vias, AC coupling capacitor and one connector, not in this order.',
    'Refer to Section 3.2.8.',
)


def snippets(change):
    for field in ('added_snippets','removed_snippets','display_added_snippets','display_removed_snippets'):
        yield from change[field]
    for field in ('replaced_snippets','display_replaced_snippets'):
        for pair in change[field]:
            yield pair['old']
            yield pair['new']


def audit(root, source_root=None, report_root=None):
    evidence = json.loads((root/'evidence.json').read_text(encoding="utf-8"))
    assert evidence['status'] == 'PASS', 'GUI did not finish successfully'
    assert evidence['source_before'] == evidence['source_after'], 'source drift during GUI execution'
    if source_root:
        assert all(hashlib.sha256((source_root/p).read_bytes()).hexdigest() == sha
                   for p,sha in evidence['source_after'].items()), 'report source differs from current source'
    run = next(r for r in evidence['runs'] if r['name']=='oif-full')
    assert run['status']=='PASS' and not run['dom_after']['startDisabled']
    assert run['dom_after']['cancelHidden'] and not run['dom_after']['running']
    report = report_root or Path(run['terminal']['report_dir'])
    for path, sha in run['artifacts'].items():
        material = report/Path(path).name
        assert hashlib.sha256(material.read_bytes()).hexdigest()==sha, f'artifact changed: {material}'

    data = json.loads((report/'protocol_diff_data.json').read_text(encoding="utf-8"))
    for side, digest in INPUTS.items():
        assert data['provenance']['inputs'][side]['sha256']==digest, 'wrong acceptance source'
    changes = data['changes']
    changed = [' '.join(s.split()) for c in changes for s in snippets(c)]
    for anchor in ANCHORS:
        assert not any(anchor in text for text in changed), f'common source mislabeled: {anchor}'
    assert not any('Optical Internetworking Forum' in s for c in changes
                   if c['role']=='technical' for s in snippets(c)), 'footer mixed into technical changes'
    catalogs = [c for c in changes if 'List of Figures' in c['report_location']]
    assert not any(c['change_type'] in ('added','deleted') for c in catalogs), 'whole shared catalog added/deleted'
    for side in INPUTS:
        assert any(s['title']=='List of Figures' for s in data[side+'_sections'])
        assert not any(v['page_number']==561 for v in data[side+'_table_visuals']), 'HCB misclassified as a table'
    assert not any(550 in v['old_pages'] or 550 in v['new_pages'] for v in data['prose_source_visuals']), 'unchanged table represented as prose strips'
    return {'status':'PASS', 'scope':'Fixed user OIF counterexamples and artifact identity only; no whole-document accuracy claim',
            'gui_seconds':run['elapsed_s'], 'report':str(report), 'source':evidence['source_after'],
            'artifacts':run['artifacts'], 'counts':run['terminal']['counts'],
            'catalog_changes':[(c['change_type'],c['old_location'],c['new_location']) for c in catalogs]}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('evidence_root',type=Path)
    parser.add_argument('--source-root',type=Path)
    parser.add_argument('--report-root',type=Path,help='Relocated artifact snapshot; original GUI hashes still apply')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=audit(args.evidence_root,args.source_root,args.report_root)
    except AssertionError as exc:
        result={'status':'FAIL','error':str(exc)}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2), encoding="utf-8")
    print('STABLE_OIF_REPORT_'+result['status'],result.get('error',''))
    return int(result['status']!='PASS')

if __name__=='__main__':
    raise SystemExit(main())
