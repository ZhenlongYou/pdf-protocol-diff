"""Create an executable v2 contract for the correspondence repair."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import yaml
ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work/content-correspondence-gate'
PYTHON = str(ROOT / '.venv/bin/python')
REQ = 'REQ-CONTENT-CORRESPONDENCE'
DEFECT = 'DEF-CONTENT-CORRESPONDENCE-20260913'


def identity(path, name, **extra):
    p = ROOT / path
    data = p.read_bytes()
    return dict(id=name, path=str(p.relative_to(ROOT)), sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), **extra)


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    sources = [identity(str(p.relative_to(ROOT)), 'SRC-' + p.stem.upper()) for p in sorted((ROOT / 'src/protocol_pdf_diff').glob('*.py'))]
    parts = ['nominal', 'boundary', 'invalid', 'adversarial', 'realistic', 'known-failure']
    paths = [f'docs/verification/fixtures/content-correspondence/{part}.json' for part in parts]
    inputs = [identity(path, 'IN-' + part.upper(), provenance='Hand-authored parameter relationships, normative language and exact text conservation, separate from production decisions.') for path, part in zip(paths, parts)]
    ids = [i['id'] for i in inputs]
    oracle = identity('docs/verification/content_correspondence_oracle.py', 'ORACLE-CONTENT', kind='independent_reference', independence_basis='No production imports; compares supplied human expectations with externally observed strings and rejects empty evidence.', production_source_ids=[s['id'] for s in sources], validator_run_ids=['RUN-ORACLE'])
    probe = identity('docs/verification/content_correspondence_probe.py', 'PROBE-CONTENT')
    mutations = []
    for name, source, transform in (
        ('LIMIT', 'reporting', lambda text: text.replace('if any(label in {"min", "minimum", "typ", "typical", "max", "maximum"}', 'if False and any(label in {"min", "minimum", "typ", "typical", "max", "maximum"}', 1)),
        ('FORMULA', 'compare', lambda text: text.replace('            result.append(prefix)\n', '            pass  # injected loss of normative introduction\n', 1)),
        ('SIGN', 'visual_ownership', lambda text: text.replace("return ''.join(kept).strip()", "return ''.join(kept).strip(' –—-')", 1)),
    ):
        current = (ROOT / f'src/protocol_pdf_diff/{source}.py').read_text()
        changed = transform(current)
        assert current != changed, name
        path = WORK / (name.lower() + '.py')
        path.write_text(changed)
        mutations.append(identity(str(path.relative_to(ROOT)), 'MUT-' + name, target_source_id='SRC-' + source.upper()))
    exe = Path(os.path.realpath(PYTHON)); data = exe.read_bytes()
    executable = dict(path=str(exe), sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))

    def run(name, role, selected, mutation=None, artifact=False):
        is_oracle = role == 'oracle'
        script = 'content_correspondence_oracle.py' if is_oracle else 'content_correspondence_probe.py'
        argv = [PYTHON, 'docs/verification/' + script, *[paths[i] for i in selected]]
        if artifact:
            argv += ['--artifact', 'work/content-correspondence-gate/public-artifact.json']
        row = dict(id=name, role=role, requirement_ids=[REQ], source_variant={'mutation_id': mutation} if mutation else {'base': 'source'}, probe_ids=[] if is_oracle else ['PROBE-CONTENT'], input_ids=[ids[i] for i in selected], argv=argv, executable=executable, cwd='.', timeout_seconds=180, expected=dict(exit_code=1 if mutation else 0, stdout=dict(contains=['CONTENT_CORRESPONDENCE_ORACLE_OK'] if is_oracle else ['CONTENT_CORRESPONDENCE_TEST', 'CONTENT_CORRESPONDENCE_CONTRACT_FAIL' if mutation else 'CONTENT_CORRESPONDENCE_CONTRACT_OK'], excludes=['Traceback', '0 tests']), stderr=dict(excludes=['Traceback', 'ModuleNotFoundError'])))
        if is_oracle:
            row['oracle_ids'] = ['ORACLE-CONTENT']
        if role == 'real_path':
            row.update(interface='report', observable='Generated PDFs pass through run_diff and write_reports; old/new Max values retain column labels in the reader facts or explicitly excluded similarity appendix.')
        if artifact:
            row['produces_artifact_ids'] = ['ART-CONTENT']
        return row

    runs = [run('RUN-ORACLE', 'oracle', list(range(6))), run('RUN-SUITE', 'suite', list(range(6))), run('RUN-CORRESPONDENCE-REAL', 'real_path', [4], artifact=True)]
    pairs = []
    for name, selected in [('LIMIT', [5]), ('FORMULA', [3]), ('SIGN', [1])]:
        runs += [run('RUN-' + name + '-RED', 'target_red', selected, 'MUT-' + name), run('RUN-' + name + '-GREEN', 'target_green', selected)]
        pairs.append(dict(id='PAIR-CONTENT-CORRESPONDENCE' if name == 'LIMIT' else 'PAIR-CONTENT-' + name, requirement_id=REQ, defect_id=DEFECT, test_id='CONTENT_CORRESPONDENCE_TEST', failure_signature='CONTENT_CORRESPONDENCE_CONTRACT_FAIL', red_run_id='RUN-' + name + '-RED', green_run_id='RUN-' + name + '-GREEN'))
    ledger = yaml.safe_load((ROOT / 'docs/verification/escaped-defects.yaml').read_text())
    rows = [d for d in ledger['escaped_defects'] if d['id'] == DEFECT]
    assert len(rows) == 1
    ledger_path = WORK / 'escaped-defects.yaml'
    ledger_path.write_text(yaml.safe_dump(dict(schema_version=1, scope_statement='Content correspondence repair', escaped_defects=rows), allow_unicode=True))
    catalog = dict(requirements=[dict(id=REQ, observable='Preserve actual normative text, signs, limits and units while separating uncertain correspondence from proved identity.', authority=dict(kind='user', locator='docs/verification/content-correspondence-20260913.md; original complete report audit'), threshold=dict(comparator='exact', value='expected semantic strings and retained source relations', unit='reader facts', locator='fixture equals and contains fields'), oracle_ids=['ORACLE-CONTENT'], partitions={p.replace('-', '_'): [i] for p, i in zip(parts, ids)})], inputs=inputs, probes=[probe], oracles=[oracle], mutations=mutations, artifacts=[dict(id='ART-CONTENT', path='work/content-correspondence-gate/public-artifact.json', producer_run_id='RUN-CORRESPONDENCE-REAL', required=True, min_size_bytes=30)], runs=runs, red_green_pairs=pairs, isolation_contracts=[dict(id='ISO-CONTENT', requirement_ids=[REQ], unit='Hand-specified failure families; original complete PDFs are separately bound real-input validation, not independent generalization samples.', selection_ids=[], certification_ids=ids)], defect_ledgers=[identity(str(ledger_path.relative_to(ROOT)), 'LEDGER-CONTENT')])
    manifest = dict(test_effectiveness_gate=dict(schema_version=2, gate_id='content-correspondence-20260913', project_root='../..', source=dict(kind='file_set', files=sources), evidence_catalog=catalog, matrix=dict(requirement=dict(requirement_ids=[REQ]), target_red=dict(pair_ids=[p['id'] for p in pairs]), independent_oracle=dict(oracle_ids=['ORACLE-CONTENT']), fault_detection=dict(pair_ids=[p['id'] for p in pairs], survivor_ids=[], defect_ledger_ids=['LEDGER-CONTENT']), input_design=dict(requirement_ids=[REQ]), isolation=dict(contract_ids=['ISO-CONTENT']), real_path=dict(run_ids=['RUN-CORRESPONDENCE-REAL']), reproducibility=dict(run_ids=[r['id'] for r in runs])), open_items=[]))
    out = ROOT / 'docs/verification/content-correspondence-evidence.json'
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(out)


if __name__ == '__main__':
    main()
