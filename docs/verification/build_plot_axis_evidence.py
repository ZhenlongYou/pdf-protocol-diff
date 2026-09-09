"""Bind plot-axis regression inputs, independent inventory and negative controls."""
import hashlib,json,os,subprocess
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[2]
WORK=ROOT/'work/plot-axis-gate'
PYTHON=str(ROOT/'.venv/bin/python')
REQ='REQ-PLOT-AXIS'

def identity(path, name, **extra):
    p=ROOT/path;data=p.read_bytes()
    return dict(id=name,path=str(p.relative_to(ROOT)),sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data),**extra)

def main():
    WORK.mkdir(parents=True,exist_ok=True)
    sources=[identity(str(p.relative_to(ROOT)),'SRC-'+p.stem.upper()) for p in sorted((ROOT/'src/protocol_pdf_diff').glob('*.py'))]
    source_id=next(s['id'] for s in sources if s['path'].endswith('/pdf_extract.py'))
    parts=['nominal','boundary','invalid','adversarial','realistic','known-failure']
    paths=[f'docs/verification/fixtures/plot-axis/{p}.json' for p in parts]
    inputs=[identity(p,'IN-'+part.upper(),provenance='Hand-specified scene and expected real-table inventory, including the user axis-grid failure.') for p,part in zip(paths,parts)]
    ids=[i['id'] for i in inputs]
    oracle=identity('docs/verification/plot_axis_oracle.py','ORACLE-PLOT',kind='independent_reference',independence_basis='Human-defined inventory assertions; imports no production module, no geometric classifier, and validates missing real data plus extra spurious tables.',production_source_ids=[s['id'] for s in sources],validator_run_ids=['RUN-ORACLE'])
    probe=identity('docs/verification/plot_axis_probe.py','PROBE-PLOT')
    baseline=WORK/'baseline.py';baseline.write_bytes(subprocess.check_output(['git','show','b7b75780c1932daf2b8ad04c3147d920a1035bc4:src/protocol_pdf_diff/pdf_extract.py'],cwd=ROOT))
    current=(ROOT/'src/protocol_pdf_diff/pdf_extract.py').read_text()
    marker='r[1] - 1 <= float(w["top"]) < bbox[1] - 1'
    assert current.count(marker)==1
    mutant=WORK/'missing-separator.py';mutant.write_text(current.replace(marker,'False and '+marker))
    mutations=[identity(str(p.relative_to(ROOT)),mid,target_source_id=source_id) for p,mid in [(baseline,'MUT-BASELINE'),(mutant,'MUT-SEPARATOR')]]
    split=WORK/'unsplit-only.py'
    split_marker='if len(word_payloads) != 1:'
    assert current.count(split_marker)==1
    split.write_text(current.replace(split_marker,'if len(payloads) != 1:'))
    mutations.append(identity(str(split.relative_to(ROOT)),'MUT-SPLIT',target_source_id=source_id))
    symbols=WORK/'semantic-symbols.py'
    symbols.write_bytes(subprocess.check_output(['git','show','b2f7dc066ed72e8c8e99b9063a6f148897f03117:src/protocol_pdf_diff/pdf_extract.py'],cwd=ROOT))
    mutations.append(identity(str(symbols.relative_to(ROOT)),'MUT-SYMBOL',target_source_id=source_id))
    exe=Path(os.path.realpath(PYTHON));raw=exe.read_bytes();exe_info=dict(path=str(exe),sha256=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw))
    def run(name,role,selected,mutation=None,artifact=False):
        is_oracle=role=='oracle';args=['docs/verification/plot_axis_oracle.py' if is_oracle else 'docs/verification/plot_axis_probe.py',*[paths[i] for i in selected]]
        if artifact:args+=['--artifact','work/plot-axis-gate/real-artifact.json']
        row=dict(id=name,role=role,requirement_ids=[REQ],source_variant={'mutation_id':mutation} if mutation else {'base':'source'},probe_ids=[] if is_oracle else ['PROBE-PLOT'],input_ids=[ids[i] for i in selected],argv=[PYTHON,*args],executable=exe_info,cwd='.',timeout_seconds=180,expected=dict(exit_code=1 if mutation else 0,stdout=dict(contains=['PLOT_AXIS_ORACLE_OK'] if is_oracle else ['PLOT_AXIS_TEST','PLOT_AXIS_CONTRACT_FAIL' if mutation else 'PLOT_AXIS_CONTRACT_OK'],excludes=['Traceback','0 tests']),stderr=dict(excludes=['Traceback','ModuleNotFoundError'])))
        if is_oracle:row['oracle_ids']=['ORACLE-PLOT']
        if role=='real_path':row.update(interface='report',observable='Source PDFs pass through run_diff and write_reports; fake axis tables absent, real Frequency rows and source page images retained.')
        if artifact:row['produces_artifact_ids']=['ART-PLOT']
        return row
    runs=[run('RUN-BASELINE-RED','target_red',[5],'MUT-BASELINE'),run('RUN-BASELINE-GREEN','target_green',[5]),run('RUN-SEPARATOR-RED','target_red',[3],'MUT-SEPARATOR'),run('RUN-SEPARATOR-GREEN','target_green',[3]),run('RUN-ORACLE','oracle',list(range(6))),run('RUN-SUITE','suite',list(range(6))),run('RUN-PLOT-REAL','real_path',[4],artifact=True)]
    runs.extend([run('RUN-SPLIT-RED','target_red',[5],'MUT-SPLIT'),run('RUN-SPLIT-GREEN','target_green',[5])])
    runs.extend([run('RUN-SYMBOL-RED','target_red',[3],'MUT-SYMBOL'),run('RUN-SYMBOL-GREEN','target_green',[3])])
    pairs=[dict(id='PAIR-PLOT-AXIS' if s=='BASELINE' else 'PAIR-PLOT-'+s,requirement_id=REQ,defect_id='DEF-PLOT-AXIS-20260910',test_id='PLOT_AXIS_TEST',failure_signature='PLOT_AXIS_CONTRACT_FAIL',red_run_id=f'RUN-{s}-RED',green_run_id=f'RUN-{s}-GREEN') for s in ['BASELINE','SEPARATOR','SPLIT','SYMBOL']]
    ledger_all=yaml.safe_load((ROOT/'docs/verification/escaped-defects.yaml').read_text())
    rows=[d for d in ledger_all['escaped_defects'] if d['id']=='DEF-PLOT-AXIS-20260910'];assert len(rows)==1
    ledger_path=WORK/'escaped-defects.yaml';ledger_path.write_text(yaml.safe_dump(dict(schema_version=1,scope_statement='Plot axis fragments falsely classified as tables',escaped_defects=rows),allow_unicode=True))
    catalog=dict(requirements=[dict(id=REQ,observable='Do not classify plot-axis font grids as deleted tables; retain real small tables, parameter data and complete page evidence.',authority=dict(kind='user',locator='docs/verification/plot-axis-20260910.md; user axis screenshot'),threshold=dict(comparator='exact',value='hand-authored expected table inventory and retained data',unit='table objects',locator='fixture expected_tables fields'),oracle_ids=['ORACLE-PLOT'],partitions={p.replace('-','_'):[i] for p,i in zip(parts,ids)})],inputs=inputs,probes=[probe],oracles=[oracle],mutations=mutations,artifacts=[dict(id='ART-PLOT',path='work/plot-axis-gate/real-artifact.json',producer_run_id='RUN-PLOT-REAL',required=True,min_size_bytes=30)],runs=runs,red_green_pairs=pairs,isolation_contracts=[dict(id='ISO-PLOT',requirement_ids=[REQ],unit='hand-specified synthetic scene; original OIF is known-failure only, no generalized accuracy claim',selection_ids=[],certification_ids=ids)],defect_ledgers=[identity(str(ledger_path.relative_to(ROOT)),'LEDGER-PLOT')])
    manifest=dict(test_effectiveness_gate=dict(schema_version=2,gate_id='plot-axis-20260910',project_root='../..',source=dict(kind='file_set',files=sources),evidence_catalog=catalog,matrix=dict(requirement=dict(requirement_ids=[REQ]),target_red=dict(pair_ids=[p['id'] for p in pairs]),independent_oracle=dict(oracle_ids=['ORACLE-PLOT']),fault_detection=dict(pair_ids=[p['id'] for p in pairs],survivor_ids=[],defect_ledger_ids=['LEDGER-PLOT']),input_design=dict(requirement_ids=[REQ]),isolation=dict(contract_ids=['ISO-PLOT']),real_path=dict(run_ids=['RUN-PLOT-REAL']),reproducibility=dict(run_ids=[r['id'] for r in runs])),open_items=[]))
    out=ROOT/'docs/verification/plot-axis-evidence.json';out.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');print(out)
if __name__=='__main__':main()
