"""Compare frozen parser evidence without legacy chapter reconstruction.

This is an evaluation entrypoint. Every nonempty parser region stays visible
in JSON; exact text pairing is not a general accuracy or ownership certificate.
"""
import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from protocol_pdf_diff.evidence_alignment import EvidenceDocument, SourceUnit, align_evidence


def read_evidence(case_dir: Path, engine: str):
    identity = json.loads((case_dir / 'source.json').read_text())
    digest = hashlib.sha256((case_dir / 'input.pdf').read_bytes()).hexdigest()
    if digest != identity['snapshot_sha256']:
        raise ValueError('Parsed PDF snapshot identity mismatch')
    folder = case_dir / engine
    path = folder / 'final-projection.json'
    if path.exists():
        data = json.loads(path.read_text())
        if hashlib.sha256((folder/'raw.json').read_bytes()).hexdigest() != data['raw_sha256']:
            raise ValueError('Raw parser output identity mismatch')
    else:
        path = folder/'result.json'
        data = json.loads(path.read_text())
    if data.get('input_sha256') is not None:
        if data['input_sha256'] != digest:
            raise ValueError('Parser output was generated from another input snapshot')
        binding = 'sha256'
    elif engine == 'docling' and 'raw_sha256' in data:
        # Historical Docling artifacts have an intrinsic truncated SHA-256
        # origin, not a complete producer receipt. Verify it and disclose that
        # narrower binding; do not silently attach an arbitrary result to a PDF.
        raw_bytes = (folder/'raw.json').read_bytes()
        if hashlib.sha256(raw_bytes).hexdigest() != data['raw_sha256']:
            raise ValueError('Raw parser output identity mismatch')
        raw_origin = json.loads(raw_bytes).get('origin', {})
        if raw_origin.get('binary_hash') != (int(digest,16) & 0xffffffffffffffff):
            raise ValueError('Docling origin does not match input snapshot')
        binding = 'historical_docling_sha256_low64'
    else:
        raise ValueError('Parser output lacks a verifiable input binding; rerun the evaluation')
    units = []
    reasons = {'parser_output_coverage_unverified'}
    if binding != 'sha256':
        reasons.add('historical_source_binding_is_truncated')
    for page in data['pages']:
        number = page['page']
        if not 1 <= number <= len(identity['physical_pages']):
            raise ValueError('Parser page outside source snapshot')
        physical = identity['physical_pages'][number-1]
        for index, block in enumerate(page['blocks']):
            if not block['text'].strip():
                continue
            risks = tuple(block.get('risks', ()))
            bbox = tuple(block['bbox']) if block.get('bbox') else None
            if bbox is None:
                risks += ('source_coordinates_missing',)
            units.append(SourceUnit(f'{digest}:p{number}:{engine}:b{index}', physical, bbox,
                                    block['text'], role=block['label'], risks=risks))
    document = EvidenceDocument(digest, tuple(units), complete=False, coverage_reasons=tuple(sorted(reasons)))
    provenance = {'source':identity,'parser_evidence_path':str(path.resolve()),
                  'parser_evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                  'input_binding':binding,
                  'adapter_sha256':data.get('adapter_sha256')}
    return document, provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('old_case', type=Path)
    parser.add_argument('new_case', type=Path)
    parser.add_argument('--engine', choices=['native','pymupdf','pymupdf4llm','docling','opendataloader'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output directory')
    old, old_source = read_evidence(args.old_case, args.engine)
    new, new_source = read_evidence(args.new_case, args.engine)
    started = time.perf_counter()
    result = align_evidence(old, new)
    elapsed = time.perf_counter()-started
    args.output.mkdir(parents=True)
    payload = {'status':'candidate', 'scope':'parser-region literal correspondence; no production accuracy claim',
               'old':asdict(old),'new':asdict(new),'alignment':asdict(result),
               'old_provenance':old_source,'new_provenance':new_source,'alignment_seconds':elapsed}
    (args.output/'evidence.json').write_text(json.dumps(payload,ensure_ascii=False))
    summary = {'status':'candidate','alignment_seconds':elapsed,'old_units':len(old.units),'new_units':len(new.units),
               'relations':dict(Counter(r.kind for r in result.relations)), 'unresolved_units':result.unresolved_unit_count,
               'paired_units':sum(len(r.old_ids)+len(r.new_ids) for r in result.relations if r.old_ids and r.new_ids),
               'old_roles':dict(Counter(u.role for u in old.units)), 'new_roles':dict(Counter(u.role for u in new.units))}
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
