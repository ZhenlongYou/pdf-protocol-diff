"""Independent occurrence-count and literal-preservation contracts."""
import unittest

from protocol_pdf_diff.evidence_alignment import EvidenceDocument, SourceUnit, align_evidence, literal_key
from protocol_pdf_diff.evidence_alignment import evidence_from_extraction
from protocol_pdf_diff.models import ExtractionResult, PageText, DocumentBlock, DocumentBlockKind
from pathlib import Path


def document(texts, *, complete=True, side="old", risks=()):
    return EvidenceDocument(("a" if side == "old" else "b") * 64,
        tuple(SourceUnit(f"{side}:{i}", i + 1, (10, 10, 500, 40), text, risks=risks) for i, text in enumerate(texts)),
        complete=complete)


class EvidenceAlignmentTests(unittest.TestCase):
    def assert_conserved(self, result, old, new):
        for side, document_ in (("old", old), ("new", new)):
            ids = [identity for relation in result.relations for identity in getattr(relation, side + "_ids")]
            self.assertCountEqual(ids, [u.occurrence_id for u in document_.units])
            self.assertEqual(len(ids), len(set(ids)))

    def test_repagination_split_merge_and_move_preserve_all_occurrences(self):
        first = "The controller shall preserve every operating condition and source measurement."
        second = "The receiver shall retain the declared limits for the selected interface."
        old = document([first, second])
        new = document([second, "The controller shall preserve every operating", "condition and source measurement."], side="new")
        result = align_evidence(old, new)
        self.assertEqual({"unresolved"}, {r.kind for r in result.relations})
        self.assertTrue(all(r.old_ids and r.new_ids for r in result.relations))
        self.assert_conserved(result, old, new)
        ordered = document(["The controller shall preserve every operating", "condition and source measurement.", second], side="new")
        self.assertTrue(all(r.kind in {"equal", "resegmented"} for r in align_evidence(old, ordered).relations))

    def test_duplicate_deleted_once_is_never_consumed_twice(self):
        repeated = "The operator shall retain the full measurement record for every independent test."
        old = document([repeated, repeated])
        new = document([repeated], side="new")
        result = align_evidence(old, new)
        self.assertGreater(result.unresolved_unit_count, 0)
        self.assert_conserved(result, old, new)
        self.assertFalse(all(r.kind == "equal" for r in result.relations))

    def test_repeated_phrase_uses_surrounding_anchors_without_losing_extra_material(self):
        a,b,c,d = [f"Boundary marker number {i}." for i in range(4)]
        phrase = 'Refer to the operating requirements.'
        old = document([a,phrase,'19',b,c,phrase,'21',d])
        new = document([a,phrase,b,c,phrase,d], side='new')
        result = align_evidence(old,new)
        for identity in ('old:1','old:5'):
            relation = next(r for r in result.relations if identity in r.old_ids)
            self.assertIn(relation.kind, {'equal','resegmented'})
            self.assertTrue(relation.new_ids)
        self.assert_conserved(result,old,new)

    def test_repeated_gap_matches_cannot_hide_order_crossings(self):
        for before,after in ((['A','R','X','R','B','C','X'], ['A','X','R','R','B','C','X']),
                             (['A','R','x','B','C','R'], ['C','A','R','y','B','R'])):
            old,new = document(before),document(after,side='new')
            result = align_evidence(old,new)
            relation = next(r for r in result.relations if 'old:1' in r.old_ids)
            self.assertEqual('unresolved',relation.kind)
            self.assert_conserved(result,old,new)

    def test_real_insertion_and_deletion_inside_unique_anchors(self):
        a, b = "Unique opening requirement.", "Unique closing requirement."
        old, new = document([a, b]), document([a, "An extra authorization is required.", b], side="new")
        result = align_evidence(old, new)
        self.assertEqual(1, sum(r.kind == "added" for r in result.relations))
        reverse = align_evidence(new, old)
        self.assertEqual(1, sum(r.kind == "deleted" for r in reverse.relations))
        self.assert_conserved(result, old, new)

    def test_numeric_sign_negation_and_decimal_changes_survive(self):
        for left, right in (("Limit +3.0 V", "Limit -3.0 V"), ("0.25 A", "0.025 A"),
                            ("shall enable", "shall not enable"), ("x ≤ 3", "x < 3")):
            with self.subTest(left=left):
                old = document(["First unique anchor.", left, "Last unique anchor."])
                new = document(["First unique anchor.", right, "Last unique anchor."], side="new")
                result = align_evidence(old, new)
                self.assertEqual(1, sum(r.kind == "modified" for r in result.relations))
                self.assertNotEqual(literal_key(left), literal_key(right))
                self.assert_conserved(result, old, new)

    def test_partial_counterpart_cannot_prove_absence(self):
        old = document(["Unique first anchor.", "Remove this requirement.", "Unique last anchor."])
        new = document(["Unique first anchor.", "Unique last anchor."], complete=False, side="new")
        result = align_evidence(old, new)
        self.assertFalse(any(r.kind == "deleted" for r in result.relations))
        self.assertEqual(1, result.unresolved_unit_count)
        self.assert_conserved(result, old, new)

    def test_ambiguous_role_and_coverage_do_not_become_equality(self):
        old = document(["Do not discard this text."], complete=False, risks=("region_ownership_conflict",))
        new = document(["Do not discard this text."], side="new")
        result = align_evidence(old, new)
        self.assertTrue(all(r.kind == "unresolved" for r in result.relations))
        self.assert_conserved(result, old, new)

    def test_invalid_identity_and_false_complete_fail_closed(self):
        with self.assertRaises(ValueError):
            document(["text"], risks=("ocr_uncertain",))
        unit = SourceUnit("duplicate", 1, (0, 0, 10, 10), "text")
        with self.assertRaises(ValueError):
            EvidenceDocument("a" * 64, (unit, unit))

    def test_moved_text_inside_a_merged_unit_is_not_deleted(self):
        a, b = "Opening boundary anchor.", "Closing boundary anchor."
        x, y = "The receiver limit remains -3.0 volts.", "The controller retains the full measurement record."
        old, new = document([a, x, b, y]), document([a, b, x + " " + y], side="new")
        result = align_evidence(old, new)
        self.assertFalse(any(r.kind == "deleted" for r in result.relations))
        self.assertFalse(any(r.kind == "added" for r in align_evidence(new, old).relations))
        self.assert_conserved(result, old, new)

    def test_partial_and_missing_coordinates_preserve_unresolved_original(self):
        for block_texts in (("Anchor A", "Anchor B"), ()):
            text = "Anchor A\nLimit -3.0 V\nAnchor B"
            blocks = tuple(DocumentBlock(1, (0, 10*i, 100, 10*i+9), DocumentBlockKind.TEXT, t, i, "test") for i, t in enumerate(block_texts))
            extraction = ExtractionResult(Path("unread.pdf"), [PageText(1, text, blocks=blocks)], total_pages=1, source_sha256="a" * 64)
            result = evidence_from_extraction(extraction)
            self.assertFalse(result.complete)
            self.assertIn("Limit -3.0 V", "\n".join(u.text for u in result.units))
            self.assertTrue(any(u.risks for u in result.units))

    def test_independent_short_values_cannot_be_declared_unchanged_after_swap(self):
        for prefix in ("", "Typical input voltage ", "典型输入电压 "):
            old = document(["Mode A", prefix + "3.0 V", "Mode B", prefix + "2.5 V"])
            new = document(["Mode A", prefix + "2.5 V", "Mode B", prefix + "3.0 V"], side="new")
            result = align_evidence(old, new)
            self.assertTrue(any(r.kind in {"modified", "unresolved"} for r in result.relations))
            self.assert_conserved(result, old, new)

    def test_unexplained_moved_replacement_or_token_split_prevents_absence(self):
        a, b = "Opening boundary anchor.", "Closing boundary anchor."
        old = document([a, "The receiver limit remains -3.0 volts.", b])
        for ending in (["The receiver limit remains -2.5 volts."], ["The receiver limit remains -3.", "0 volts."]):
            new = document([a, b, *ending], side="new")
            self.assertFalse(any(r.kind == "deleted" for r in align_evidence(old, new).relations))
            self.assertFalse(any(r.kind == "added" for r in align_evidence(new, old).relations))

    def test_coverage_preserves_sequence_and_both_conflicting_source_views(self):
        for page_text, block_texts in (("Limit +3.0 V", ["Limit 3.0 V", "Header +"]),
                                      ("Thresholds 1 23 V", ["Thresholds 12 3 V"]),
                                      ("Body\nMissing -3.0 V", ["Revision A", "Body"])):
            blocks = tuple(DocumentBlock(1, (0, i*10, 100, i*10+9), DocumentBlockKind.TEXT, t, i, "test") for i, t in enumerate(block_texts))
            extraction = ExtractionResult(Path("unread.pdf"), [PageText(1, page_text, blocks=blocks)], total_pages=1, source_sha256="a"*64)
            result = evidence_from_extraction(extraction)
            self.assertFalse(result.complete)
            self.assertEqual(page_text, result.units[0].text)
            self.assertEqual(block_texts, [u.text for u in result.alternate_source_views])


if __name__ == "__main__":
    unittest.main()
