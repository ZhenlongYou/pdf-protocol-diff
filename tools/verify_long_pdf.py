"""Execute frozen long-PDF regression partitions through real product paths.

Used by the executable evidence gate; no network, private standards, or mocks
replace the real spawned comparison in the publication/cancellation checks.
"""
from __future__ import annotations
import argparse
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]


def real_path():
    from protocol_pdf_diff.sample_data import write_demo_pdfs
    from protocol_pdf_diff.webview_gui import ProtocolDiffWebApi
    from tests.test_comparison_job import wait_for
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        old, new = write_demo_pdfs(root / "input")
        api = ProtocolDiffWebApi()
        config = {"old_pdf": str(old), "new_pdf": str(new), "output_dir": str(root / "out")}
        assert api.run_comparison(config)["ok"]
        assert api.cancel_comparison()["ok"]
        wait_for(lambda: api.get_run_state()["type"] in ("cancelled", "error"))
        assert api.get_run_state()["type"] == "cancelled"
        assert api.run_comparison(config)["ok"]
        wait_for(lambda: api.get_run_state()["type"] in ("success", "error"), 30)
        result = api.get_run_state()
        assert result["type"] == "success", result
        html = Path(result["html_path"])
        data = json.loads((html.parent / "protocol_diff_data.json").read_text())
        assert (data["old_total_pages"], data["new_total_pages"]) == (4, 5)
        changes = json.dumps(data["changes"])
        for phrase in ("20 working days", "15 working days", "Documentation"):
            assert phrase in changes, phrase
        assert "<html" in html.read_text().lower()
        assert not list((root / "out").glob(".protocol-diff-*"))
        witness = {"cancelled_then_restarted": True, "page_counts": [4, 5],
                   "delivery_changed_20_to_15_days": True, "documentation_added": True,
                   "html_and_json_reopened": True, "private_output_removed": True}
    output = ROOT / "work" / "long_pdf_evidence" / "real_path.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(witness, sort_keys=True) + "\n")
    print("LONG_PDF_REAL_PATH_OK")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", choices=["performance", "cancel", "oracle", "real"], required=True)
    parser.add_argument("--oracle-material")
    parser.add_argument("fixtures", nargs="*")
    args = parser.parse_args()
    print("LONG_PDF_BEHAVIOR_TEST", flush=True)
    if args.check == "real":
        real_path()
        return 0
    if args.check == "oracle":
        from docs.verification import long_pdf_legacy_oracle
        if not args.oracle_material or Path(args.oracle_material).resolve() != Path(long_pdf_legacy_oracle.__file__).resolve():
            raise ValueError("The declared frozen oracle must be the executed module")
    tests = []
    for filename in args.fixtures:
        fixture = json.loads(Path(filename).read_text())
        tests.extend(fixture[args.check])
    if not tests: raise ValueError("No executable cases selected")
    suite = unittest.defaultTestLoader.loadTestsFromNames(list(dict.fromkeys(tests)))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    if not result.wasSuccessful():
        print("LONG_PDF_ASSERTION_FAILED")
        for _test, detail in result.failures + result.errors:
            print(detail.splitlines()[-1])
        return 1
    print(f"LONG_PDF_CHECK_OK {args.check} cases={result.testsRun}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
