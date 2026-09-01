"""Build the executable evidence manifest for visual-review layout defects."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON_ARGV = str(ROOT / ".venv" / "bin" / "python")
PYTHON_REAL = Path(os.path.realpath(sys.executable))
FIXTURES = [
    "nominal",
    "boundary",
    "invalid",
    "adversarial",
    "realistic",
    "known-failure",
]


def identity(path: str, item_id: str, **extra: object) -> dict[str, object]:
    absolute = ROOT / path
    data = absolute.read_bytes()
    return {
        "id": item_id,
        "path": path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        **extra,
    }


def executable() -> dict[str, object]:
    data = PYTHON_REAL.read_bytes()
    return {
        "path": str(PYTHON_REAL),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def run(
    run_id: str,
    role: str,
    requirements: list[str],
    argv: list[str],
    *,
    source_variant: dict[str, str] | None = None,
    expected_exit: int = 0,
    contains: list[str],
    input_ids: list[str] | None = None,
    oracle_ids: list[str] | None = None,
    produces: list[str] | None = None,
    interface: str | None = None,
    observable: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": run_id,
        "role": role,
        "requirement_ids": requirements,
        "source_variant": source_variant or {"base": "source"},
        "probe_ids": [] if oracle_ids or role == "real_path" else ["PROBE-LAYOUT-001"],
        "input_ids": input_ids or [],
        "argv": [PYTHON_ARGV, *argv],
        "executable": executable(),
        "cwd": ".",
        "timeout_seconds": 60,
        "expected": {
            "exit_code": expected_exit,
            "stdout": {"contains": contains, "excludes": ["0 tests", "Traceback"]},
            "stderr": {"excludes": ["ModuleNotFoundError", "Traceback"]},
        },
    }
    if oracle_ids:
        payload["oracle_ids"] = oracle_ids
    if produces:
        payload["produces_artifact_ids"] = produces
    if interface:
        payload["interface"] = interface
    if observable:
        payload["observable"] = observable
    return payload


def main() -> int:
    fixture_paths = [f"docs/verification/fixtures/visual-layout-{name}.json" for name in FIXTURES]
    input_ids = [f"IN-LAYOUT-{name.upper().replace('-', '_')}" for name in FIXTURES]
    requirements = [
        "REQ-VISUAL-REVIEW-SCALE-002",
        "REQ-VISUAL-REVIEW-CROP-003",
        "REQ-VISUAL-REVIEW-DISCLOSURE-004",
    ]
    package_paths = sorted((ROOT / "src" / "protocol_pdf_diff").glob("*.py"))
    source_ids = {
        "visual_preview.py": "SRC-VISUAL-PREVIEW",
        "visual_watchdog.py": "SRC-VISUAL-WATCHDOG",
        "reporting.py": "SRC-REPORTING",
    }
    sources = [
        identity(
            path.relative_to(ROOT).as_posix(),
            source_ids.get(path.name, f"SRC-PKG-{index:02d}"),
        )
        for index, path in enumerate(package_paths, start=1)
    ]
    sources.append(identity("docs/verification/visual_review_layout_real_path.py", "SRC-REAL"))
    inputs = [
        identity(path, input_id, provenance=f"frozen {name} visual-layout fixture")
        for path, input_id, name in zip(fixture_paths, input_ids, FIXTURES, strict=True)
    ]
    probe = identity(
        "docs/verification/visual_review_layout_probe.py",
        "PROBE-LAYOUT-001",
    )
    oracle = identity(
        "docs/verification/visual_review_layout_oracle.py",
        "ORACLE-LAYOUT-001",
        kind="closed_form",
        independence_basis="The oracle checks fixture geometry and intrinsic-width arithmetic without importing production code.",
        production_source_ids=["SRC-VISUAL-PREVIEW", "SRC-VISUAL-WATCHDOG", "SRC-REPORTING"],
        validator_run_ids=["RUN-LAYOUT-ORACLE"],
    )
    mutation = identity(
        "docs/verification/mutants/visual_review_layout.py",
        "MUT-LAYOUT-LEGACY",
        target_source_id="SRC-VISUAL-PREVIEW",
    )
    artifact = identity(
        "docs/verification/out/visual-review-layout-real-path.json",
        "ART-LAYOUT-REAL",
        producer_run_id="RUN-VISUAL-REVIEW-REAL",
        required=True,
        min_size_bytes=1,
    )
    artifact.pop("size_bytes")
    known_failure = ["IN-LAYOUT-KNOWN_FAILURE"]
    fixture_args = fixture_paths
    runs = [
        run(
            "RUN-LAYOUT-CROP-RED",
            "target_red",
            ["REQ-VISUAL-REVIEW-CROP-003"],
            ["docs/verification/visual_review_layout_probe.py", "--check", "crop", fixture_paths[-1]],
            source_variant={"mutation_id": "MUT-LAYOUT-LEGACY"},
            expected_exit=1,
            contains=["VISUAL_REVIEW_LAYOUT_TEST", "HORIZONTAL_CONTEXT_CROPPED"],
            input_ids=known_failure,
        ),
        run(
            "RUN-LAYOUT-CROP-GREEN",
            "target_green",
            ["REQ-VISUAL-REVIEW-CROP-003"],
            ["docs/verification/visual_review_layout_probe.py", "--check", "crop", fixture_paths[-1]],
            contains=["VISUAL_REVIEW_LAYOUT_TEST", "VISUAL_REVIEW_LAYOUT_OK"],
            input_ids=known_failure,
        ),
        run(
            "RUN-LAYOUT-SCALE-RED",
            "target_red",
            ["REQ-VISUAL-REVIEW-SCALE-002"],
            ["docs/verification/visual_review_layout_probe.py", "--check", "scale"],
            source_variant={"mutation_id": "MUT-LAYOUT-LEGACY"},
            expected_exit=1,
            contains=["VISUAL_REVIEW_LAYOUT_TEST", "VISUAL_REVIEW_UPSCALED"],
        ),
        run(
            "RUN-LAYOUT-SCALE-GREEN",
            "target_green",
            ["REQ-VISUAL-REVIEW-SCALE-002"],
            ["docs/verification/visual_review_layout_probe.py", "--check", "scale"],
            contains=["VISUAL_REVIEW_LAYOUT_TEST", "VISUAL_REVIEW_LAYOUT_OK"],
        ),
        run(
            "RUN-LAYOUT-DISCLOSURE-RED",
            "target_red",
            ["REQ-VISUAL-REVIEW-DISCLOSURE-004"],
            ["docs/verification/visual_review_layout_probe.py", "--check", "disclosure"],
            source_variant={"mutation_id": "MUT-LAYOUT-LEGACY"},
            expected_exit=1,
            contains=["VISUAL_REVIEW_LAYOUT_TEST", "VISUAL_MASK_NOT_COLLAPSED"],
        ),
        run(
            "RUN-LAYOUT-DISCLOSURE-GREEN",
            "target_green",
            ["REQ-VISUAL-REVIEW-DISCLOSURE-004"],
            ["docs/verification/visual_review_layout_probe.py", "--check", "disclosure"],
            contains=["VISUAL_REVIEW_LAYOUT_TEST", "VISUAL_REVIEW_LAYOUT_OK"],
        ),
        run(
            "RUN-LAYOUT-ORACLE",
            "oracle",
            requirements,
            ["docs/verification/visual_review_layout_oracle.py", *fixture_args],
            contains=["ORACLE_VISUAL_REVIEW_LAYOUT_OK"],
            input_ids=input_ids,
            oracle_ids=["ORACLE-LAYOUT-001"],
        ),
        run(
            "RUN-VISUAL-REVIEW-REAL",
            "real_path",
            requirements,
            ["docs/verification/visual_review_layout_real_path.py"],
            contains=["REAL_VISUAL_REVIEW_LAYOUT_OK"],
            input_ids=["IN-LAYOUT-REALISTIC"],
            produces=["ART-LAYOUT-REAL"],
            interface="report",
            observable="A generated HTML report retains full horizontal evidence and caps rendered width at intrinsic image width.",
        ),
        run(
            "RUN-LAYOUT-SUITE",
            "suite",
            requirements,
            ["docs/verification/visual_review_layout_probe.py", "--check", "all", *fixture_args],
            contains=["VISUAL_REVIEW_LAYOUT_TEST", "VISUAL_REVIEW_LAYOUT_OK"],
            input_ids=input_ids,
        ),
    ]
    partition_map = {
        name.replace("-", "_"): [input_id]
        for name, input_id in zip(FIXTURES, input_ids, strict=True)
    }
    requirement_items = [
        {
            "id": "REQ-VISUAL-REVIEW-SCALE-002",
            "observable": "Visual-review images render no wider than their intrinsic pixel width while still shrinking to narrow containers.",
            "authority": {"kind": "user", "locator": "2026-09-02 screenshot showing abnormally enlarged visual-review evidence"},
            "threshold": {"comparator": "exact", "value": "client_width=min(natural_width, container_width)", "unit": "CSS pixels", "locator": "user-visible HTML report"},
            "oracle_ids": ["ORACLE-LAYOUT-001"],
            "partitions": partition_map,
        },
        {
            "id": "REQ-VISUAL-REVIEW-CROP-003",
            "observable": "Every visual-review source crop retains the complete horizontal page width so left-side table labels remain visible.",
            "authority": {"kind": "user", "locator": "2026-09-02 screenshot showing the left half of a table missing"},
            "threshold": {"comparator": "exact", "value": "preview_width=source_page_width", "unit": "pixels", "locator": "decoded report evidence image"},
            "oracle_ids": ["ORACLE-LAYOUT-001"],
            "partitions": partition_map,
        },
        {
            "id": "REQ-VISUAL-REVIEW-DISCLOSURE-004",
            "observable": "The pixel-level mask is collapsed by default, clearly labeled as technical review, and explained as non-semantic evidence.",
            "authority": {"kind": "user", "locator": "2026-09-02 request to treat the mask as a technical intermediate rather than primary reader content"},
            "threshold": {"comparator": "exact", "value": "details:not-open; summary=technical review; limitation text present", "unit": "HTML disclosure state", "locator": "user-visible HTML report"},
            "oracle_ids": ["ORACLE-LAYOUT-001"],
            "partitions": partition_map,
        },
    ]
    pairs = [
        {
            "id": "PAIR-VISUAL-REVIEW-SCALE-002",
            "requirement_id": "REQ-VISUAL-REVIEW-SCALE-002",
            "defect_id": "DEF-VISUAL-REVIEW-SCALE-002",
            "test_id": "VISUAL_REVIEW_LAYOUT_TEST",
            "failure_signature": "VISUAL_REVIEW_UPSCALED",
            "red_run_id": "RUN-LAYOUT-SCALE-RED",
            "green_run_id": "RUN-LAYOUT-SCALE-GREEN",
        },
        {
            "id": "PAIR-VISUAL-REVIEW-CROP-003",
            "requirement_id": "REQ-VISUAL-REVIEW-CROP-003",
            "defect_id": "DEF-VISUAL-REVIEW-CROP-003",
            "test_id": "VISUAL_REVIEW_LAYOUT_TEST",
            "failure_signature": "HORIZONTAL_CONTEXT_CROPPED",
            "red_run_id": "RUN-LAYOUT-CROP-RED",
            "green_run_id": "RUN-LAYOUT-CROP-GREEN",
        },
        {
            "id": "PAIR-VISUAL-REVIEW-DISCLOSURE-004",
            "requirement_id": "REQ-VISUAL-REVIEW-DISCLOSURE-004",
            "defect_id": "DEF-VISUAL-REVIEW-DISCLOSURE-004",
            "test_id": "VISUAL_REVIEW_LAYOUT_TEST",
            "failure_signature": "VISUAL_MASK_NOT_COLLAPSED",
            "red_run_id": "RUN-LAYOUT-DISCLOSURE-RED",
            "green_run_id": "RUN-LAYOUT-DISCLOSURE-GREEN",
        },
    ]
    ledger = identity(
        "docs/verification/visual-review-layout-escaped-defects.yaml",
        "LEDGER-VISUAL-REVIEW-LAYOUT",
    )
    manifest = {
        "test_effectiveness_gate": {
            "schema_version": 2,
            "gate_id": "visual-review-layout-20260902",
            "project_root": "../..",
            "source": {"kind": "file_set", "files": sources},
            "evidence_catalog": {
                "requirements": requirement_items,
                "inputs": inputs,
                "probes": [probe],
                "oracles": [oracle],
                "mutations": [mutation],
                "artifacts": [artifact],
                "runs": runs,
                "red_green_pairs": pairs,
                "isolation_contracts": [
                    {
                        "id": "ISO-VISUAL-REVIEW-LAYOUT",
                        "requirement_ids": requirements,
                        "unit": "visual-layout fixture case",
                        "selection_ids": [],
                        "certification_ids": input_ids,
                    }
                ],
                "defect_ledgers": [ledger],
            },
            "matrix": {
                "requirement": {"requirement_ids": requirements},
                "target_red": {"pair_ids": [pair["id"] for pair in pairs]},
                "independent_oracle": {"oracle_ids": ["ORACLE-LAYOUT-001"]},
                "fault_detection": {"pair_ids": [pair["id"] for pair in pairs], "survivor_ids": [], "defect_ledger_ids": [ledger["id"]]},
                "input_design": {"requirement_ids": requirements},
                "isolation": {"contract_ids": ["ISO-VISUAL-REVIEW-LAYOUT"]},
                "real_path": {"run_ids": ["RUN-VISUAL-REVIEW-REAL"]},
                "reproducibility": {"run_ids": [item["id"] for item in runs]},
            },
            "open_items": [],
        }
    }
    output = ROOT / "docs" / "verification" / "visual-review-layout-evidence.json"
    output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
