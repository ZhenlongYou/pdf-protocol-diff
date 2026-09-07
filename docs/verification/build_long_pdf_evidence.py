"""Freeze executable evidence for long-PDF computation and cancellation.

Generated mutants and receipts belong under ignored work/, not shipped code.
The manifest is refreshed after a production or test change before execution.
"""
from __future__ import annotations
import json
import yaml
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from docs.verification.build_visual_review_layout_evidence import identity, executable, PYTHON_ARGV

PARTITIONS = ['nominal', 'boundary', 'invalid', 'adversarial', 'realistic', 'known_failure']
REQS = ['REQ-LONG-PDF-PERFORMANCE-005', 'REQ-DESKTOP-CANCEL-006']


def main():
    source_paths = sorted([*ROOT.glob('src/protocol_pdf_diff/*.py'), ROOT/'src/protocol_pdf_diff/webui/index.html',
        ROOT/'main.py', ROOT/'gui_app.py', ROOT/'tests/__init__.py',
        ROOT/'tests/test_comparison_job.py', ROOT/'tests/test_long_pdf_performance.py',
        ROOT/'docs/verification/long_pdf_legacy_oracle.py',ROOT/'docs/verification/escaped-defects.yaml'])
    sources = [identity(str(p.relative_to(ROOT)), 'SRC-LONG-'+str(i)) for i,p in enumerate(source_paths)]
    source_ids = {x['path']:x['id'] for x in sources}
    fixture_paths = [f'docs/verification/fixtures/long-pdf-{p}.json' for p in PARTITIONS]
    inputs = [identity(path, 'IN-LONG-'+part.upper(), provenance='Frozen executable scenario selection: '+part)
              for path,part in zip(fixture_paths, PARTITIONS)]
    probe = identity('tools/verify_long_pdf.py', 'PROBE-LONG')
    oracle = identity('docs/verification/long_pdf_legacy_oracle.py', 'ORACLE-LONG', kind='reference_implementation',
        independence_basis='Frozen db45c03 historical implementations share only unchanged grammar/geometry primitives; separate grid-DP and explicit process/report observations independently check new behavior. This does not certify all historical recognition semantics.',
        production_source_ids=[source_ids['src/protocol_pdf_diff/'+x] for x in ['compare.py','pdf_extract.py','text_utils.py','comparison_job.py','webview_gui.py']],
        validator_run_ids=['RUN-LONG-ORACLE'])
    runs=[];pairs=[];mutations=[]
    def run(run_id,role,reqs,check,paths,mutant=None):
        item=dict(id=run_id,role=role,requirement_ids=reqs,source_variant={'mutation_id':mutant} if mutant else {'base':'source'},
            probe_ids=[probe['id']],input_ids=[x['id'] for x in inputs if x['path'] in paths],
            argv=[PYTHON_ARGV,'tools/verify_long_pdf.py','--check',check,*paths],executable=executable(),cwd='.',timeout_seconds=90,
            expected={'exit_code':1 if mutant else 0,'stdout':{'contains':['LONG_PDF_BEHAVIOR_TEST','LONG_PDF_ASSERTION_FAILED' if mutant else ('LONG_PDF_REAL_PATH_OK' if check=='real' else 'LONG_PDF_CHECK_OK')], 'excludes':['Traceback','0 tests']},'stderr':{'excludes':['Traceback','ModuleNotFoundError']}})
        runs.append(item);return item
    variants=[
        ('NUMBERS','text_utils.py','performance',0,'parsed = _parse_normalized_number_word_phrase(normalized_tokens, index)','parsed = parse_number_word_phrase(tokens, index)'),
        ('LCS','compare.py','performance',0,'if max(len(left), len(right)) > 4096:','if False:  # injected loss of long-key bound'),
        ('OCR','pdf_extract.py','performance',0,'config="--psm 6", timeout=60','config="--psm 6"'),
        ('CANCEL','webview_gui.py','cancel',1,'self._cancel_event.set()','pass  # injected ignored cancellation request'),
    ]
    generated=ROOT/'work/long_pdf_evidence/mutants';generated.mkdir(parents=True,exist_ok=True)
    for tag,module,check,index,old,new in variants:
        path='src/protocol_pdf_diff/'+module
        text=(ROOT/path).read_text();assert old in text,(tag,old)
        mutant=generated/(tag.lower()+'.py');mutant.write_text(text.replace(old,new,1))
        mid='MUT-LONG-'+tag
        mutations.append(identity(str(mutant.relative_to(ROOT)),mid,target_source_id=source_ids[path]))
        rid,gid='RUN-LONG-'+tag+'-RED','RUN-LONG-'+tag+'-GREEN'
        for ident,role,mutation in [(rid,'target_red',mid),(gid,'target_green',None)]:run(ident,role,[REQS[index]],check,[fixture_paths[-1]],mutation)
        pairs.append(dict(id='PAIR-LONG-'+tag,requirement_id=REQS[index],defect_id='DEF-LONG-'+tag,
            test_id='LONG_PDF_BEHAVIOR_TEST',failure_signature='LONG_PDF_ASSERTION_FAILED',red_run_id=rid,green_run_id=gid))
    item=run('RUN-LONG-ORACLE','oracle',REQS,'oracle',fixture_paths);item['oracle_ids']=[oracle['id']];item['argv'][4:4]=['--oracle-material',oracle['path']]
    run('RUN-LONG-PERFORMANCE','suite',[REQS[0]],'performance',fixture_paths)
    run('RUN-LONG-CANCEL','suite',[REQS[1]],'cancel',fixture_paths)
    item=run('RUN-LONG-REAL','real_path',REQS,'real',[fixture_paths[4]])
    item.update(interface='gui',observable='Production asynchronous GUI facade cancels, restarts, and publishes a parser-reopened 4/5 page report retaining the known 20-to-15-day change and added Documentation.',produces_artifact_ids=['ART-LONG-REAL'])
    artifact=dict(id='ART-LONG-REAL',path='work/long_pdf_evidence/real_path.json',producer_run_id='RUN-LONG-REAL',required=True,min_size_bytes=100)
    authoritative=ROOT/'docs/verification/escaped-defects.yaml'
    scoped=json.loads(json.dumps(yaml.safe_load(authoritative.read_text())))
    scoped['escaped_defects']=[x for x in scoped['escaped_defects'] if x['id'].startswith('DEF-LONG-')]
    scoped['scope_statement']='Task-scoped view of docs/verification/escaped-defects.yaml; authoritative source is included in source identity.'
    ledger_path=ROOT/'work/long_pdf_evidence/escaped-defects.json'
    ledger_path.write_text(json.dumps(scoped,indent=2)+'\n')
    ledger=identity(str(ledger_path.relative_to(ROOT)),'LEDGER-LONG')
    requirements=[]
    for index,observable in enumerate([
        'Skip redundant normalization, impossible geometry pairs and impossible long-key alignment without changing the frozen recognition results; each table OCR call has a finite budget.',
        'Cancel, worker crash, cleanup retry, window close and restart preserve process ownership and publish only complete reports.']):
        requirements.append(dict(id=REQS[index],observable=observable,authority={'kind':'user','locator':'2026-09-07 long PDF 89-minute report and request to implement performance/cancel fixes'},
            threshold={'comparator':'exact','value':'All selected behavioral assertions and independent oracles pass','unit':'behavior','locator':'tests/test_long_pdf_performance.py and tests/test_comparison_job.py'},
            oracle_ids=[oracle['id']],partitions={p:[x['id']] for p,x in zip(PARTITIONS,inputs)}))
    gate=dict(schema_version=2,gate_id='long-pdf-performance-cancel-20260907',project_root='../..',source={'kind':'file_set','files':sources},
        evidence_catalog=dict(requirements=requirements,inputs=inputs,probes=[probe],oracles=[oracle],mutations=mutations,artifacts=[artifact],runs=runs,red_green_pairs=pairs,
            isolation_contracts=[dict(id='ISO-LONG',requirement_ids=REQS,unit='independent task and frozen scenario',selection_ids=[],certification_ids=[x['id'] for x in inputs])],defect_ledgers=[ledger]),
        matrix=dict(requirement={'requirement_ids':REQS},target_red={'pair_ids':[x['id'] for x in pairs]},independent_oracle={'oracle_ids':[oracle['id']]},
            fault_detection={'pair_ids':[x['id'] for x in pairs],'survivor_ids':[],'defect_ledger_ids':[ledger['id']]},input_design={'requirement_ids':REQS},
            isolation={'contract_ids':['ISO-LONG']},real_path={'run_ids':['RUN-LONG-REAL']},reproducibility={'run_ids':[x['id'] for x in runs]}),open_items=[])
    output=ROOT/'docs/verification/long-pdf-evidence.json';output.write_text(json.dumps({'test_effectiveness_gate':gate},indent=2)+'\n');print(output)


if __name__=='__main__':main()
