"""Execute a bounded occurrence contract against frozen manual expectations."""
from dataclasses import asdict
import json
from pathlib import Path
import sys
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from protocol_pdf_diff.evidence_alignment import EvidenceDocument, SourceUnit, align_evidence
from docs.verification.occurrence_oracle import validate


def main():
    print("OCCURRENCE_BEHAVIOR_TEST")
    records = []
    try:
        real = "--real" in sys.argv
        for path in [arg for arg in sys.argv[1:] if arg != "--real"]:
            fixture = json.loads(Path(path).read_text())
            if real:
                from protocol_pdf_diff.sample_data import write_multipage_text_pdf
                root = ROOT / "work/occurrence-evidence/real"
                root.mkdir(parents=True, exist_ok=True)
                root = Path(tempfile.mkdtemp(prefix="run-", dir=root))
                for side in ("old", "new"):
                    write_multipage_text_pdf(root / (side + ".pdf"), [fixture[side]])
                command = [sys.executable, str(ROOT / "tools/compare_document_evidence.py"),
                           str(root / "old.pdf"), str(root / "new.pdf"), "--output", str(root / "report")]
                run = subprocess.run(command, capture_output=True, text=True, timeout=30)
                assert run.returncode == 0, run.stderr
                payload = json.loads((root / "report/evidence.json").read_text())
                maps = {side:{u["occurrence_id"]:f"{side}:{i}" for i,u in enumerate(payload[side]["units"])} for side in ("old", "new")}
                observed = {side+"_texts":[u["text"].strip() for u in payload[side]["units"]] for side in ("old", "new")}
                observed["relations"] = [{"kind":r["kind"], **{side+"_ids":[maps[side][i] for i in r[side+"_ids"]] for side in ("old", "new")}} for r in payload["alignment"]["relations"]]
                validate(fixture, observed)
                report = (root / "report/report.html").read_text()
                assert "+3.0 V" in report and "-3.0 V" in report
                (ROOT / "work/occurrence-evidence/real-path.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
                print("OCCURRENCE_REAL_PATH_OK")
                continue
            try:
                documents = []
                for side in ("old", "new"):
                    units = tuple(SourceUnit(f"{side}:{0 if fixture.get('duplicate_ids') else i}",
                                  i+1, (10,10,500,40), text) for i,text in enumerate(fixture[side]))
                    documents.append(EvidenceDocument(("a" if side == "old" else "b")*64, units, complete=True))
                result = align_evidence(*documents)
                observed = {"relations":[asdict(r) for r in result.relations],
                            "old_texts":[u.text for u in documents[0].units],
                            "new_texts":[u.text for u in documents[1].units]}
            except ValueError as exc:
                observed = {"exception":type(exc).__name__}
            validate(fixture, observed)
            records.append({"input":path, "observed":observed})
    except AssertionError as exc:
        print("OCCURRENCE_ASSERTION_FAILED", str(exc))
        return 1
    if records:
        target = ROOT / 'work/occurrence-evidence/api-path.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    print("OCCURRENCE_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
