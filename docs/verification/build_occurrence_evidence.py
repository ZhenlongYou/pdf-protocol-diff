"""Freeze the candidate's limited regression contract, not PDF accuracy claims."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from docs.verification.build_visual_review_layout_evidence import identity, executable, PYTHON_ARGV


def main():
    parts = ['nominal', 'boundary', 'invalid', 'adversarial', 'realistic', 'known_failure']
    req = 'REQ-OCCURRENCE-CANDIDATE-CONTRACT'
    paths = sorted([*ROOT.glob('src/protocol_pdf_diff/*.py'), ROOT/'tools/compare_document_evidence.py',
                    ROOT/'tools/verify_occurrence_contract.py', ROOT/'docs/verification/occurrence_oracle.py'])
    sources = [identity(str(p.relative_to(ROOT)), f'SRC-OCC-{i}') for i,p in enumerate(paths)]
    ids = {s['path']:s['id'] for s in sources}
    inputs = [identity(f'docs/verification/fixtures/occurrence-{p}.json', f'IN-OCC-{p}',
                       provenance='Manual regression case; not a document-family generalization sample') for p in parts]
    probe = identity('tools/verify_occurrence_contract.py', 'PROBE-OCC')
    oracle = identity('docs/verification/occurrence_oracle.py', 'ORACLE-OCC', kind='manual_expected_values',
                      independence_basis='Fixed per-occurrence states, source strings and exact identity counts; no production imports or matching algorithm. A separate executable rejects corrupted evidence. Scope is the six regression scenarios, not PDF parsing or general document accuracy.',
                      production_source_ids=[ids['src/protocol_pdf_diff/evidence_alignment.py']], validator_run_ids=['RUN-OCC-ORACLE'])
    runs, pairs, mutations = [], [], []
    def run(rid, role, selected, mutation=None):
        row = dict(id=rid, role=role, requirement_ids=[req], probe_ids=[probe['id']],
                   input_ids=[i['id'] for i in selected], source_variant={'mutation_id':mutation} if mutation else {'base':'source'},
                   argv=[PYTHON_ARGV, probe['path'], *[i['path'] for i in selected]], executable=executable(), cwd='.', timeout_seconds=60,
                   expected={'exit_code':1 if mutation else 0, 'stdout':{'contains':['OCCURRENCE_BEHAVIOR_TEST', 'OCCURRENCE_ASSERTION_FAILED' if mutation else 'OCCURRENCE_CHECK_OK'], 'excludes':['Traceback']}, 'stderr':{'excludes':['Traceback','ModuleNotFoundError']}})
        runs.append(row)
        return row
    source_path = 'src/protocol_pdf_diff/evidence_alignment.py'
    source = (ROOT/source_path).read_text()
    folder = ROOT/'work/occurrence-evidence/mutants'; folder.mkdir(parents=True, exist_ok=True)
    for name, before, after, case in [
        ('sign', 'return " ".join(text.split())', 'return " ".join(text.split()).replace("-", "").replace("+", "")', 4),
        ('absence', 'new.complete and new_fully_matched and before[0]', 'new.complete and before[0]', 5),
        ('ownership', 'if a0 <= before[0] or b0 <= before[1] or a1 >= after[0] or b1 >= after[1]:', 'if False:  # injected loss of owner-context safeguard', 3),
    ]:
        assert source.count(before) == 1, name
        target = folder/(name+'.py'); target.write_text(source.replace(before, after, 1))
        mid = 'MUT-OCC-'+name
        mutations.append(identity(str(target.relative_to(ROOT)), mid, target_source_id=ids[source_path]))
        red, green = 'RUN-OCC-'+name+'-RED', 'RUN-OCC-'+name+'-GREEN'
        run(red, 'target_red', [inputs[case]], mid)
        run(green, 'target_green', [inputs[case]])
        pairs.append(dict(id='PAIR-OCC-'+name, requirement_id=req, defect_id='CANDIDATE-'+name,
                          test_id='OCCURRENCE_BEHAVIOR_TEST', failure_signature='OCCURRENCE_ASSERTION_FAILED', red_run_id=red, green_run_id=green))
    run('RUN-OCC-SUITE', 'suite', inputs)
    row = run('RUN-OCC-API', 'real_path', inputs)
    row.update(interface='api', observable='Invoke the public candidate EvidenceDocument/SourceUnit/align_evidence API on all six frozen cases and reopen validated per-occurrence outputs.', produces_artifact_ids=['ART-OCC-API'])
    row = run('RUN-OCC-ORACLE', 'oracle', inputs)
    row.update(probe_ids=[], oracle_ids=[oracle['id']], argv=[PYTHON_ARGV, oracle['path'], *[i['path'] for i in inputs]])
    row['expected']['stdout']['contains'] = ['OCCURRENCE_ORACLE_REJECTS_CORRUPTION']
    row = run('RUN-OCC-REAL', 'real_path', [inputs[4]])
    row['argv'].insert(2, '--real')
    row.update(interface='cli', observable='Fresh real PDF pair reaches the candidate CLI, retains both signed limits in reopened HTML/JSON, and conserves source occurrences.', produces_artifact_ids=['ART-OCC-REAL'])
    row['expected']['stdout']['contains'].append('OCCURRENCE_REAL_PATH_OK')
    artifact = dict(id='ART-OCC-REAL', path='work/occurrence-evidence/real-path.json', producer_run_id='RUN-OCC-REAL', required=True, min_size_bytes=100)
    api_artifact = dict(id='ART-OCC-API', path='work/occurrence-evidence/api-path.json', producer_run_id='RUN-OCC-API', required=True, min_size_bytes=100)
    ledger_path = ROOT/'work/occurrence-evidence/candidate-regressions.json'
    ledger_path.write_text(json.dumps({'schema_version':1, 'scope_statement':'Candidate API review regressions and directed sign-loss fault only. User-reported OIF production defects remain open in docs/verification/escaped-defects.yaml; this ledger does not close them.',
        'escaped_defects':[dict(id=p['defect_id'], acceptance_id=req, user_symptom='Prevent unsupported difference conclusions in the candidate API.',
        discovered_via='candidate_review_or_directed_fault_injection', failure_mechanism=p['defect_id'],
        red_green_pair_id=p['id'], regression_test_id=p['test_id'], public_path_rerun_id='RUN-OCC-API', state='verified') for p in pairs]},indent=2)+'\n')
    ledger = identity(str(ledger_path.relative_to(ROOT)), 'LEDGER-OCC-CANDIDATE')
    requirement = dict(id=req, observable='On six frozen regression cases, candidate matching conserves occurrences and literal signs, surfaces crossed ownership and unresolved moved replacements, and rejects invalid identities. This is not general PDF accuracy acceptance.',
                       authority={'kind':'user', 'locator':'2026-09-07 request for generality, accuracy and implementation'},
                       threshold={'comparator':'exact','value':'Every declared per-occurrence state and source string equals the manual fixture', 'unit':'regression assertion', 'locator':'docs/verification/fixtures/occurrence-*.json'},
                       oracle_ids=[oracle['id']], partitions={p:[i['id']] for p,i in zip(parts, inputs)})
    gate = dict(schema_version=2, gate_id='occurrence-candidate-regression-20260907', project_root='../..', source={'kind':'file_set','files':sources},
                evidence_catalog=dict(requirements=[requirement],inputs=inputs,probes=[probe],oracles=[oracle],mutations=mutations,artifacts=[artifact, api_artifact],runs=runs,red_green_pairs=pairs,
                    isolation_contracts=[dict(id='ISO-OCC', requirement_ids=[req], unit='frozen regression scenario; no document-family generalization claimed', selection_ids=[], certification_ids=[i['id'] for i in inputs])],defect_ledgers=[ledger]),
                matrix=dict(requirement={'requirement_ids':[req]},target_red={'pair_ids':[p['id'] for p in pairs]}, independent_oracle={'oracle_ids':[oracle['id']]},
                    fault_detection={'pair_ids':[p['id'] for p in pairs], 'survivor_ids':[], 'defect_ledger_ids':[ledger['id']]},input_design={'requirement_ids':[req]},isolation={'contract_ids':['ISO-OCC']},
                    real_path={'run_ids':['RUN-OCC-REAL','RUN-OCC-API']},reproducibility={'run_ids':[r['id'] for r in runs]}),open_items=[])
    target = ROOT/'docs/verification/occurrence-evidence.json'
    target.write_text(json.dumps({'test_effectiveness_gate':gate},indent=2)+'\n')
    print(target)


if __name__ == '__main__':
    main()
