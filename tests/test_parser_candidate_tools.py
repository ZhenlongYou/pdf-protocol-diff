"""Public-process and independent-oracle tests for candidate tooling."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.evaluate_parser_candidates import check_expectations
from tools.evaluate_parser_candidates import docling_pages
from tools.compare_parser_evidence import read_evidence
from types import SimpleNamespace
from protocol_pdf_diff.sample_data import write_multipage_text_pdf

ROOT = Path(__file__).resolve().parents[1]


class ParserCandidateToolTests(unittest.TestCase):
    def test_docling_cross_page_provenance_uses_disjoint_character_spans(self):
        class Box:
            def to_top_left_origin(self, height):
                return self
            def as_tuple(self):
                return (10, 10, 300, 30)
        text = "First page. Second page."
        item = SimpleNamespace(label="text", text=text, self_ref="#/texts/7", prov=[
            SimpleNamespace(page_no=1, charspan=(0, 11), bbox=Box()),
            SimpleNamespace(page_no=2, charspan=(12, 24), bbox=Box())])
        document = SimpleNamespace(pages={1:SimpleNamespace(size=SimpleNamespace(height=792)),
                                         2:SimpleNamespace(size=SimpleNamespace(height=792))},
                                   iterate_items=lambda **kwargs: [(item, 0)])
        pages = docling_pages(document, included_content_layers={"body"})
        self.assertEqual(["First page.", "Second page."], [p["text"].strip() for p in pages])
        self.assertNotEqual(pages[0]["blocks"][0]["id"], pages[1]["blocks"][0]["id"])
        item.label = 'list_item'
        item.text = 'Retain the source requirement.'
        item.prov = [SimpleNamespace(page_no=1, charspan=(0, len(item.text)+3), bbox=Box())]
        pages = docling_pages(document, included_content_layers={'body'})
        self.assertEqual(item.text, pages[0]['text'].strip())
        self.assertIn('source_character_span_basis_differs', pages[0]['blocks'][0]['risks'])

    def test_oracle_rejects_absent_page_wrong_order_and_wrong_region(self):
        page = {"page": 1, "text": "Limit -3.0 V End Start", "blocks": [
            {"label": "table", "bbox": [0, 0, 100, 100], "text": "Limit -3.0 V"}]}
        checks = check_expectations([page], {"pages": [
            {"page": 1, "contains": ["Limit +3.0 V"], "ordered": ["Start", "End"],
             "excluded_regions": [{"labels": ["table"], "point": [50, 50]}]},
            {"page": 2, "absent": ["missing"]}]})
        self.assertEqual([False, True, False, False, False], [c["pass"] for c in checks])

    def test_public_pdf_candidate_keeps_parameter_change_and_exact_source_ids(self):
        with tempfile.TemporaryDirectory(prefix="candidate public ") as temporary:
            root = Path(temporary)
            old = write_multipage_text_pdf(root / "old.pdf", [[
                "First unique anchor retained for independent verification.",
                "The operating limit is +3.0 V.",
                "Last unique anchor retained for independent verification."]])
            new = write_multipage_text_pdf(root / "new.pdf", [[
                "First unique anchor retained for independent verification.",
                "The operating limit is -3.0 V.",
                "Last unique anchor retained for independent verification."]])
            output = root / "candidate report"
            run = subprocess.run([sys.executable, str(ROOT / "tools/compare_document_evidence.py"),
                                  str(old), str(new), "--output", str(output)],
                                 cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, run.returncode, run.stderr)
            payload = json.loads((output / "evidence.json").read_text())
            self.assertEqual(hashlib.sha256(old.read_bytes()).hexdigest(), payload["old"]["source_sha256"])
            self.assertEqual(hashlib.sha256(new.read_bytes()).hexdigest(), payload["new"]["source_sha256"])
            for side in ("old", "new"):
                expected = [u["occurrence_id"] for u in payload[side]["units"]]
                observed = [i for r in payload["alignment"]["relations"] for i in r[side + "_ids"]]
                self.assertEqual(Counter(expected), Counter(observed))
            relations = payload["alignment"]["relations"]
            self.assertEqual(1, sum(r["kind"] == "modified" for r in relations))
            html = (output / "report.html").read_text()
            self.assertIn("+3.0 V", html)
            self.assertIn("-3.0 V", html)

    def test_benchmark_empty_oracle_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = write_multipage_text_pdf(root / "input.pdf", [["A literal source requirement."]])
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"cases": [{"id": "empty-oracle", "path": str(pdf), "pages": [1]}]}))
            output = root / "result"
            command = [sys.executable, str(ROOT / "tools/evaluate_parser_candidates.py"), "--manifest", str(manifest),
                       "--output", str(output), "--engines", "pymupdf"]
            run = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(1, run.returncode, run.stderr)
            result = json.loads((output / "summary.json").read_text())
            self.assertEqual(manifest.read_bytes(), (output / "manifest.json").read_bytes())
            self.assertEqual(hashlib.sha256(manifest.read_bytes()).hexdigest(), result["manifest_sha256"])
            self.assertTrue(result["native_source_files"])
            self.assertEqual("INCONCLUSIVE", result["runs"][0]["status"])
            self.assertEqual([], result["runs"][0]["checks"])
            rerun = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(0, rerun.returncode)
            self.assertEqual(result, json.loads((output / "summary.json").read_text()))
            document, provenance = read_evidence(output/'empty-oracle','pymupdf')
            self.assertEqual('sha256', provenance['input_binding'])
            self.assertTrue(document.units)
            parser_output = output/'empty-oracle/pymupdf/result.json'
            wrong = json.loads(parser_output.read_text())
            wrong['input_sha256'] = '0'*64
            parser_output.write_text(json.dumps(wrong))
            with self.assertRaisesRegex(ValueError, 'another input'):
                read_evidence(output/'empty-oracle','pymupdf')
            del wrong['input_sha256']
            parser_output.write_text(json.dumps(wrong))
            with self.assertRaisesRegex(ValueError, 'lacks a verifiable'):
                read_evidence(output/'empty-oracle','pymupdf')


if __name__ == "__main__":
    unittest.main()
