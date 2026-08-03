"""Mandatory generated PDF mutations across unrelated document structures."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from protocol_pdf_diff.compare import run_diff
from protocol_pdf_diff.models import DiffOptions, DiffResult
from protocol_pdf_diff.sample_data import write_multipage_text_pdf


class GeneratedMutationCorpusTests(unittest.TestCase):
    """Prove change recall without depending on private vendor PDFs."""

    def test_semantic_mutations_survive_multiple_heading_families(self) -> None:
        """Formula, negation, identifier, and limit changes must all remain visible."""

        shared_lines = [
            "The document shall preserve every declared condition limit and responsibility."
            for _ in range(9)
        ]
        cases = (
            (
                "numeric-standard",
                ["1 Calculation", *shared_lines, "GAIN = 2 + 3"],
                ["1 Calculation", *shared_lines, "GAIN = 2 + 4"],
                ("GAIN = 2 + 3", "GAIN = 2 + 4"),
                ("1",),
                "1 Calculation",
            ),
            (
                "named-section-policy",
                ["Section 2 Security", *shared_lines, "The service shall allow remote access."],
                ["Section 2 Security", *shared_lines, "The service shall not allow remote access."],
                ("shall allow", "shall not allow"),
                ("Section 2",),
                "Section 2 Security",
            ),
            (
                "multipart-procedure",
                ["Part I Operations", "1 Modes", *shared_lines, "MODE_FAST"],
                ["Part I Operations", "1 Modes", *shared_lines, "MODE_SAFE"],
                ("MODE_FAST", "MODE_SAFE"),
                ("Part I", "1"),
                "Part I Operations / 1 Modes",
            ),
            (
                "annex-method",
                ["Annex A Test methods", "A.1 Limits", *shared_lines, "Limit = 12.5 ms"],
                ["Annex A Test methods", "A.1 Limits", *shared_lines, "Limit = 10.5 ms"],
                ("12.5 ms", "10.5 ms"),
                ("Annex A", "A.1"),
                "Annex A Test methods / A.1 Limits",
            ),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for case_id, old_lines, new_lines, anchors, expected_path, expected_location in cases:
                with self.subTest(case=case_id):
                    old_pdf = write_multipage_text_pdf(root / f"{case_id}-old.pdf", [old_lines])
                    new_pdf = write_multipage_text_pdf(root / f"{case_id}-new.pdf", [new_lines])

                    result = run_diff(old_pdf, new_pdf, DiffOptions())
                    changed_text = _changed_text(result)

                    self.assertTrue(result.changes, case_id)
                    self.assertEqual("reliable", result.assessment.state.value, case_id)
                    self.assertEqual(1, len(result.changes), case_id)
                    change = result.changes[0]
                    self.assertEqual("modified", change.change_type, case_id)
                    self.assertEqual(expected_location, change.report_location, case_id)
                    self.assertEqual(expected_path, change.old_section.number_path, case_id)
                    self.assertEqual(expected_path, change.new_section.number_path, case_id)
                    self.assertTrue(
                        all(
                            not section.section_id.startswith("P")
                            for section in [*result.old_sections, *result.new_sections]
                        ),
                        case_id,
                    )
                    for anchor in anchors:
                        self.assertIn(anchor, changed_text, case_id)


def _changed_text(result: DiffResult) -> str:
    """Flatten user-visible prose findings for mutation-anchor assertions."""

    return "\n".join(
        [
            *(text for change in result.changes for text in change.removed_snippets),
            *(text for change in result.changes for text in change.added_snippets),
            *(pair.old for change in result.changes for pair in change.replaced_snippets),
            *(pair.new for change in result.changes for pair in change.replaced_snippets),
        ]
    )


if __name__ == "__main__":
    unittest.main()
