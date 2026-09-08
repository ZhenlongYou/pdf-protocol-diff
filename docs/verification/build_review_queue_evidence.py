"""Build strict executable evidence for the reader review queue."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON_ARGV = str(ROOT / ".venv" / "bin" / "python")
PYTHON_REAL = Path(os.path.realpath(sys.executable))
FIXTURES = ("nominal", "boundary", "invalid", "adversarial", "realistic", "known-failure")


def identity(path: str, item_id: str, **extra: object) -> dict[str, object]:
    data = (ROOT / path).read_bytes()
    return {"id": item_id, "path": path, "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data), **extra}


def executable() -> dict[str, object]:
    data = PYTHON_REAL.read_bytes()
    return {"path": str(PYTHON_REAL), "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def run(run_id: str, role: str, argv: list[str], *, source_variant: dict[str, str] | None = None, exit_code: int = 0, contains: list[str], input_ids: list[str], oracle_ids: list[str] | None = None, produces: list[str] | None = None, interface: str | None = None, observable: str | None = None) -> dict[str, object]:
    item: dict[str, object] = {
        "id": run_id,
        "role": role,
        "requirement_ids": ["REQ-REVIEW-QUEUE-005"],
        "source_variant": source_variant or {"base": "source"},
        "probe_ids": [] if role in {"oracle", "real_path"} else ["PROBE-REVIEW-QUEUE-005"],
        "input_ids": input_ids,
        "argv": [PYTHON_ARGV, *argv],
        "executable": executable(),
        "cwd": ".",
        "timeout_seconds": 60,
        "expected": {"exit_code": exit_code, "stdout": {"contains": contains, "excludes": ["0 tests", "Traceback"]}, "stderr": {"excludes": ["ModuleNotFoundError", "Traceback"]}},
    }
    if oracle_ids:
        item["oracle_ids"] = oracle_ids
    if produces:
        item["produces_artifact_ids"] = produces
    if interface:
        item["interface"] = interface
    if observable:
        item["observable"] = observable
    return item


def main() -> int:
    fixture_paths = [f"docs/verification/fixtures/review-queue-{name}.json" for name in FIXTURES]
    input_ids = [f"IN-REVIEW-QUEUE-{name.upper().replace('-', '_')}" for name in FIXTURES]
    package_paths = sorted((ROOT / "src" / "protocol_pdf_diff").glob("*.py"))
    sources = [identity(path.relative_to(ROOT).as_posix(), "SRC-REVIEW-QUEUE" if path.name == "review_queue.py" else f"SRC-PKG-{index:02d}") for index, path in enumerate(package_paths, 1)]
    sources.extend([
        identity("docs/verification/review_queue_probe.py", "SRC-REVIEW-QUEUE-PROBE"),
        identity("docs/verification/review_queue_real_path.py", "SRC-REVIEW-QUEUE-REAL"),
    ])
    inputs = [identity(path, item_id, provenance=f"frozen {name} review-queue case") for path, item_id, name in zip(fixture_paths, input_ids, FIXTURES, strict=True)]
    probe = identity("docs/verification/review_queue_probe.py", "PROBE-REVIEW-QUEUE-005")
    oracle = identity(
        "docs/verification/review_queue_oracle.py", "ORACLE-REVIEW-QUEUE-005",
        kind="closed_form",
        independence_basis="The oracle validates frozen status/action expectations without importing the review queue or report renderer.",
        production_source_ids=["SRC-REVIEW-QUEUE"],
        validator_run_ids=["RUN-REVIEW-QUEUE-ORACLE"],
    )
    mutation = identity("docs/verification/mutants/review_queue_empty.py", "MUT-REVIEW-QUEUE-EMPTY", target_source_id="SRC-REVIEW-QUEUE")
    artifact = {"id": "ART-REVIEW-QUEUE-REAL", "path": "docs/verification/out/review-queue-real-path.json", "producer_run_id": "RUN-REVIEW-QUEUE-REAL", "required": True, "min_size_bytes": 1}
    known_failure = ["IN-REVIEW-QUEUE-KNOWN_FAILURE"]
    runs = [
        run("RUN-REVIEW-QUEUE-RED", "target_red", ["docs/verification/review_queue_probe.py", fixture_paths[-1]], source_variant={"mutation_id": "MUT-REVIEW-QUEUE-EMPTY"}, exit_code=1, contains=["REVIEW_QUEUE_TEST", "REVIEW_QUEUE_STATUS_OR_LINK_LOST"], input_ids=known_failure),
        run("RUN-REVIEW-QUEUE-GREEN", "target_green", ["docs/verification/review_queue_probe.py", fixture_paths[-1]], contains=["REVIEW_QUEUE_TEST", "REVIEW_QUEUE_OK"], input_ids=known_failure),
        run("RUN-REVIEW-QUEUE-ORACLE", "oracle", ["docs/verification/review_queue_oracle.py", *fixture_paths], contains=["ORACLE_REVIEW_QUEUE_OK"], input_ids=input_ids, oracle_ids=["ORACLE-REVIEW-QUEUE-005"]),
        run("RUN-REVIEW-QUEUE-REAL", "real_path", ["docs/verification/review_queue_real_path.py"], contains=["REAL_REVIEW_QUEUE_OK"], input_ids=["IN-REVIEW-QUEUE-REALISTIC"], produces=["ART-REVIEW-QUEUE-REAL"], interface="report", observable="A generated HTML and JSON report expose typed review actions that link to their original evidence cards."),
        run("RUN-REVIEW-QUEUE-SUITE", "suite", ["docs/verification/review_queue_probe.py", *fixture_paths], contains=["REVIEW_QUEUE_TEST", "REVIEW_QUEUE_OK"], input_ids=input_ids),
    ]
    partitions = {name.replace("-", "_"): [item_id] for name, item_id in zip(FIXTURES, input_ids, strict=True)}
    requirements = [{
        "id": "REQ-REVIEW-QUEUE-005",
        "observable": "The HTML/JSON report provides separate, source-linked actions for detected content changes, uncertain evidence and incomplete coverage without summing unlike units as technical changes.",
        "authority": {"kind": "user", "locator": "2026-09-08 request to retain paired evidence but reduce manual difference hunting"},
        "threshold": {"comparator": "exact", "value": "typed status counts and stable # evidence links", "unit": "review action", "locator": "generated report queue and protocol_diff_data.json"},
        "oracle_ids": ["ORACLE-REVIEW-QUEUE-005"],
        "partitions": partitions,
    }]
    pair = {"id": "PAIR-REVIEW-QUEUE-005", "requirement_id": "REQ-REVIEW-QUEUE-005", "defect_id": "DEF-REVIEW-QUEUE-005", "test_id": "REVIEW_QUEUE_TEST", "failure_signature": "REVIEW_QUEUE_STATUS_OR_LINK_LOST", "red_run_id": "RUN-REVIEW-QUEUE-RED", "green_run_id": "RUN-REVIEW-QUEUE-GREEN"}
    ledger = identity("docs/verification/review-queue-escaped-defects.yaml", "LEDGER-REVIEW-QUEUE")
    manifest = {
        "test_effectiveness_gate": {
            "schema_version": 2,
            "gate_id": "review-queue-20260908",
            "project_root": "../..",
            "source": {"kind": "file_set", "files": sources},
            "evidence_catalog": {
                "requirements": requirements,
                "inputs": inputs,
                "probes": [probe],
                "oracles": [oracle],
                "mutations": [mutation],
                "artifacts": [artifact],
                "runs": runs,
                "red_green_pairs": [pair],
                "isolation_contracts": [{
                    "id": "ISO-REVIEW-QUEUE-005",
                    "requirement_ids": ["REQ-REVIEW-QUEUE-005"],
                    "unit": "frozen review-queue case",
                    "selection_ids": [],
                    "certification_ids": input_ids,
                }],
                "defect_ledgers": [ledger],
            },
            "matrix": {
                "requirement": {"requirement_ids": ["REQ-REVIEW-QUEUE-005"]},
                "target_red": {"pair_ids": [pair["id"]]},
                "independent_oracle": {"oracle_ids": [oracle["id"]]},
                "fault_detection": {
                    "pair_ids": [pair["id"]],
                    "survivor_ids": [],
                    "defect_ledger_ids": [ledger["id"]],
                },
                "input_design": {"requirement_ids": ["REQ-REVIEW-QUEUE-005"]},
                "isolation": {"contract_ids": ["ISO-REVIEW-QUEUE-005"]},
                "real_path": {"run_ids": ["RUN-REVIEW-QUEUE-REAL"]},
                "reproducibility": {"run_ids": [item["id"] for item in runs]},
            },
            "open_items": [],
        }
    }
    output = ROOT / "docs" / "verification" / "review-queue-evidence.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
