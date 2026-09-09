"""Freeze the screenshot-first execution plan and meaningful negative controls.

Generated full-file mutation snapshots stay in ignored work/, not in source
history. The receipt binds their hashes and the precise production file set.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
BASE = '219a794f3c725e80c2d3f2c4d7126e735a670d7f'
WORK = ROOT/'work/screenshot-first-gate'
REQ = 'REQ-SCREENSHOT-FIRST'
PYTHON = str(ROOT/'.venv/bin/python')


def identity(path, item_id, **extra):
    path = Path(path)
    if not path.is_absolute():
        path = ROOT/path
    data = path.read_bytes()
    return dict(id=item_id, path=path.relative_to(ROOT).as_posix(),
                sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), **extra)


def run(run_id, role, argv, inputs, *, mutation=None, fail=False, oracle=False, artifact=False):
    exe = Path(os.path.realpath(PYTHON)); data = exe.read_bytes()
    row = dict(id=run_id, role=role, requirement_ids=[REQ],
               source_variant={'mutation_id': mutation} if mutation else {'base': 'source'},
               probe_ids=[] if oracle else ['PROBE-SCREENSHOT'], input_ids=inputs,
               argv=[PYTHON,*argv], executable=dict(path=str(exe),sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data)),
               cwd='.', timeout_seconds=120,
               expected=dict(exit_code=1 if fail else 0,stdout=dict(
                   contains=['SCREENSHOT_ORACLE_OK'] if oracle else ['SCREENSHOT_FIRST_TEST','SCREENSHOT_CONTRACT_FAIL' if fail else 'SCREENSHOT_CONTRACT_OK'],
                   excludes=['Traceback','0 tests']),stderr=dict(excludes=['Traceback','ModuleNotFoundError'])))
    if oracle:
        row['oracle_ids']=['ORACLE-SCREENSHOT']
    if role == 'real_path':
        row.update(interface='report',observable='Independently drawn PDF pages pass through run_diff and write_reports; source-page context, short-change images and folded text are checked.')
    if artifact:
        row['produces_artifact_ids']=['ART-SCREENSHOT-REAL']
    return row


def main():
    WORK.mkdir(parents=True,exist_ok=True)
    source_ids = {}
    sources=[]
    for p in sorted((ROOT/'src/protocol_pdf_diff').glob('*.py')):
        item_id='SRC-'+p.stem.upper().replace('_','-')
        source_ids[p.name]=item_id
        sources.append(identity(p,item_id))
    parts=['nominal','boundary','invalid','adversarial','realistic','known-failure']
    fixture_paths=[f'docs/verification/fixtures/screenshot-first/{part}.json' for part in parts]
    input_ids=['IN-SCREENSHOT-'+part.upper() for part in parts]
    inputs=[identity(p,i,provenance='Hand-authored user contract cases; expected facts do not call production normalization.') for p,i in zip(fixture_paths,input_ids)]
    probe=identity('docs/verification/screenshot_first_probe.py','PROBE-SCREENSHOT')
    oracle=identity('docs/verification/screenshot_first_oracle.py','ORACLE-SCREENSHOT',kind='independent_reference',
        independence_basis='Display partitions and complete source page dimensions are hand specified. Oracle imports no production module and rejects missing images, lost raw facts and missing source warnings.',
        production_source_ids=list(source_ids.values()),validator_run_ids=['RUN-SCREENSHOT-ORACLE'])
    mutations=[]
    for name in ('reporting.py','prose_source_visuals.py','pdf_extract.py'):
        dest=WORK/('baseline-'+name)
        dest.write_bytes(subprocess.check_output(['git','show',f'{BASE}:src/protocol_pdf_diff/{name}'],cwd=ROOT))
        mutations.append(identity(dest,'MUT-'+name.removesuffix('.py').upper(),target_source_id=source_ids[name]))
    for name, target in [('CONTEXT','prose_source_visuals.py'),('GLYPHS','reporting.py')]:
        dest=WORK/('review-'+name+'.py')
        dest.write_bytes(subprocess.check_output(['git','show','238e2f79c1acc6edac24dbdb63b0eecf2bcee804:src/protocol_pdf_diff/'+target],cwd=ROOT))
        mutations.append(identity(dest,'MUT-'+name,target_source_id=source_ids[target]))
    runs=[];pairs=[]
    for suffix,mut,fixture_index in [('REPORT','MUT-REPORTING',3),('SOURCE','MUT-PROSE_SOURCE_VISUALS',4),('TABLE','MUT-PDF_EXTRACT',4),('CONTEXT','MUT-CONTEXT',3),('GLYPHS','MUT-GLYPHS',3)]:
        red='RUN-SCREENSHOT-'+suffix+'-RED';green='RUN-SCREENSHOT-'+suffix+'-GREEN'
        args=['docs/verification/screenshot_first_probe.py',fixture_paths[fixture_index]]
        runs.extend([run(red,'target_red',args,[input_ids[fixture_index]],mutation=mut,fail=True),
                     run(green,'target_green',args,[input_ids[fixture_index]])])
        pairs.append(dict(id='PAIR-SCREENSHOT-FIRST' if suffix=='REPORT' else 'PAIR-SCREENSHOT-'+suffix,
                          requirement_id=REQ,defect_id='DEF-SCREENSHOT-FIRST-20260910',test_id='SCREENSHOT_FIRST_TEST',
                          failure_signature='SCREENSHOT_CONTRACT_FAIL',red_run_id=red,green_run_id=green))
    runs.append(run('RUN-SCREENSHOT-ORACLE','oracle',['docs/verification/screenshot_first_oracle.py',*fixture_paths],input_ids,oracle=True))
    runs.append(run('RUN-SCREENSHOT-SUITE','suite',['docs/verification/screenshot_first_probe.py',*fixture_paths],input_ids))
    artifact_path='work/screenshot-first-gate/real-artifact.json'
    runs.append(run('RUN-SCREENSHOT-REAL','real_path',['docs/verification/screenshot_first_probe.py',fixture_paths[4],'--artifact',artifact_path],[input_ids[4]],artifact=True))
    ledger_all=yaml.safe_load((ROOT/'docs/verification/escaped-defects.yaml').read_text())
    scoped=[d for d in ledger_all['escaped_defects'] if d['id']=='DEF-SCREENSHOT-FIRST-20260910']
    assert len(scoped)==1
    ledger_path=WORK/'escaped-defects.yaml';ledger_path.write_text(yaml.safe_dump({'schema_version':1,'scope_statement':'Screenshot-first and display-one report appendix','escaped_defects':scoped},allow_unicode=True))
    ledger=identity(ledger_path,'LEDGER-SCREENSHOT')
    catalog=dict(requirements=[dict(id=REQ,observable='Show full source images before folded text; retain paired display-one facts in an end appendix excluded from main counts.',
                    authority=dict(kind='user',locator='docs/verification/screenshot-first-20260910.md; current task user three screenshot requirements'),
                    threshold=dict(comparator='exact',value='all frozen screenshot-preservation and exclusion assertions',unit='report facts',locator='fixture expect objects'),
                    oracle_ids=['ORACLE-SCREENSHOT'],partitions={p.replace('-','_'):[i] for p,i in zip(parts,input_ids)})],
                 inputs=inputs,probes=[probe],oracles=[oracle],mutations=mutations,
                 artifacts=[dict(id='ART-SCREENSHOT-REAL',path=artifact_path,producer_run_id='RUN-SCREENSHOT-REAL',required=True,min_size_bytes=30)],
                 runs=runs,red_green_pairs=pairs,
                 isolation_contracts=[dict(id='ISO-SCREENSHOT',requirement_ids=[REQ],unit='hand-authored case, no parameter selection',selection_ids=[],certification_ids=input_ids)],
                 defect_ledgers=[ledger])
    manifest=dict(test_effectiveness_gate=dict(schema_version=2,gate_id='screenshot-first-20260910',project_root='../..',
        source=dict(kind='file_set',files=sources),evidence_catalog=catalog,
        matrix=dict(requirement=dict(requirement_ids=[REQ]),target_red=dict(pair_ids=[p['id'] for p in pairs]),
                    independent_oracle=dict(oracle_ids=['ORACLE-SCREENSHOT']),fault_detection=dict(pair_ids=[p['id'] for p in pairs],survivor_ids=[],defect_ledger_ids=['LEDGER-SCREENSHOT']),
                    input_design=dict(requirement_ids=[REQ]),isolation=dict(contract_ids=['ISO-SCREENSHOT']),real_path=dict(run_ids=['RUN-SCREENSHOT-REAL']),
                    reproducibility=dict(run_ids=[r['id'] for r in runs])),open_items=[]))
    out=ROOT/'docs/verification/screenshot-first-evidence.json';out.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(out)


if __name__=='__main__':
    main()
