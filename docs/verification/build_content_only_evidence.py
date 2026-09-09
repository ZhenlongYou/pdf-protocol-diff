"""Freeze the content-only execution plan and meaningful negative controls.

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
BASE = '8becb689f32edaa30d9a7c6f37afdf6bbca32f3d'
WORK = ROOT/'work/content-only-gate'
REQ = 'REQ-CONTENT-ONLY'
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
               probe_ids=[] if oracle else ['PROBE-CONTENT'], input_ids=inputs,
               argv=[PYTHON,*argv], executable=dict(path=str(exe),sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data)),
               cwd='.', timeout_seconds=120,
               expected=dict(exit_code=1 if fail else 0,stdout=dict(
                   contains=['CONTENT_ORACLE_OK'] if oracle else ['CONTENT_ONLY_TEST','CONTENT_CONTRACT_FAIL' if fail else 'CONTENT_CONTRACT_OK'],
                   excludes=['Traceback','0 tests']),stderr=dict(excludes=['Traceback','ModuleNotFoundError'])))
    if oracle:
        row['oracle_ids']=['ORACLE-CONTENT']
    if role == 'real_path':
        row.update(interface='report',observable='Real independently drawn PDF pages pass through run_diff and write_reports; content changes survive and publication/cosmetic changes disappear.')
    if artifact:
        row['produces_artifact_ids']=['ART-CONTENT-REAL']
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
    fixture_paths=[f'docs/verification/fixtures/content-only/{part}.json' for part in parts]
    input_ids=['IN-CONTENT-'+part.upper() for part in parts]
    inputs=[identity(p,i,provenance='Hand-authored user contract cases; expected facts do not call production normalization.') for p,i in zip(fixture_paths,input_ids)]
    probe=identity('docs/verification/content_only_probe.py','PROBE-CONTENT')
    oracle=identity('docs/verification/content_only_oracle.py','ORACLE-CONTENT',kind='independent_reference',
        independence_basis='Expected content-presence/absence and value preservation are hand specified in fixtures. Oracle imports no production module and checks negative controls.',
        production_source_ids=list(source_ids.values()),validator_run_ids=['RUN-CONTENT-ORACLE'])
    mutations=[]
    for name in ('reporting.py','sectioning.py'):
        dest=WORK/('baseline-'+name)
        dest.write_bytes(subprocess.check_output(['git','show',f'{BASE}:src/protocol_pdf_diff/{name}'],cwd=ROOT))
        mutations.append(identity(dest,'MUT-'+name.removesuffix('.py').upper(),target_source_id=source_ids[name]))
    # A plausible over-filter removes real values as well as cosmetic noise.
    p=ROOT/'src/protocol_pdf_diff/content_equivalence.py'
    text=p.read_text(); target='    left, right = compact(old), compact(new)'
    assert text.count(target)==1
    dest=WORK/'overfilter.py';dest.write_text(text.replace(target,'    return True  # deliberate semantic-loss negative control\n'+target))
    mutations.append(identity(dest,'MUT-OVERFILTER',target_source_id=source_ids[p.name]))
    runs=[];pairs=[]
    for suffix,mut,fixture_index in [('REPORT','MUT-REPORTING',5),('METADATA','MUT-SECTIONING',5),('PRESERVE','MUT-OVERFILTER',3)]:
        red='RUN-CONTENT-'+suffix+'-RED';green='RUN-CONTENT-'+suffix+'-GREEN'
        args=['docs/verification/content_only_probe.py',fixture_paths[fixture_index]]
        runs.extend([run(red,'target_red',args,[input_ids[fixture_index]],mutation=mut,fail=True),
                     run(green,'target_green',args,[input_ids[fixture_index]])])
        pairs.append(dict(id='PAIR-CONTENT-ONLY' if suffix=='REPORT' else 'PAIR-CONTENT-'+suffix,
                          requirement_id=REQ,defect_id='DEF-CONTENT-ONLY-20260909',test_id='CONTENT_ONLY_TEST',
                          failure_signature='CONTENT_CONTRACT_FAIL',red_run_id=red,green_run_id=green))
    runs.append(run('RUN-CONTENT-ORACLE','oracle',['docs/verification/content_only_oracle.py',*fixture_paths],input_ids,oracle=True))
    runs.append(run('RUN-CONTENT-SUITE','suite',['docs/verification/content_only_probe.py',*fixture_paths],input_ids))
    artifact_path='work/content-only-gate/real-artifact.json'
    runs.append(run('RUN-CONTENT-REAL','real_path',['docs/verification/content_only_probe.py',fixture_paths[4],'--artifact',artifact_path],[input_ids[4]],artifact=True))
    ledger_all=yaml.safe_load((ROOT/'docs/verification/escaped-defects.yaml').read_text())
    scoped=[d for d in ledger_all['escaped_defects'] if d['id']=='DEF-CONTENT-ONLY-20260909']
    assert len(scoped)==1
    ledger_path=WORK/'escaped-defects.yaml';ledger_path.write_text(yaml.safe_dump({'schema_version':1,'scope_statement':'Substantive content report only','escaped_defects':scoped},allow_unicode=True))
    ledger=identity(ledger_path,'LEDGER-CONTENT')
    catalog=dict(requirements=[dict(id=REQ,observable='Suppress only excluded metadata/cosmetic differences and retain changed substantive requirements in actual reports.',
                    authority=dict(kind='user',locator='docs/verification/content-only-20260909.md; current task user request and global email clarification'),
                    threshold=dict(comparator='exact',value='all frozen content-preservation and exclusion assertions',unit='report facts',locator='fixture expect objects'),
                    oracle_ids=['ORACLE-CONTENT'],partitions={p.replace('-','_'):[i] for p,i in zip(parts,input_ids)})],
                 inputs=inputs,probes=[probe],oracles=[oracle],mutations=mutations,
                 artifacts=[dict(id='ART-CONTENT-REAL',path=artifact_path,producer_run_id='RUN-CONTENT-REAL',required=True,min_size_bytes=30)],
                 runs=runs,red_green_pairs=pairs,
                 isolation_contracts=[dict(id='ISO-CONTENT',requirement_ids=[REQ],unit='hand-authored case, no parameter selection',selection_ids=[],certification_ids=input_ids)],
                 defect_ledgers=[ledger])
    manifest=dict(test_effectiveness_gate=dict(schema_version=2,gate_id='content-only-20260909',project_root='../..',
        source=dict(kind='file_set',files=sources),evidence_catalog=catalog,
        matrix=dict(requirement=dict(requirement_ids=[REQ]),target_red=dict(pair_ids=[p['id'] for p in pairs]),
                    independent_oracle=dict(oracle_ids=['ORACLE-CONTENT']),fault_detection=dict(pair_ids=[p['id'] for p in pairs],survivor_ids=[],defect_ledger_ids=['LEDGER-CONTENT']),
                    input_design=dict(requirement_ids=[REQ]),isolation=dict(contract_ids=['ISO-CONTENT']),real_path=dict(run_ids=['RUN-CONTENT-REAL']),
                    reproducibility=dict(run_ids=[r['id'] for r in runs])),open_items=[]))
    out=ROOT/'docs/verification/content-only-evidence.json';out.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(out)


if __name__=='__main__':
    main()
