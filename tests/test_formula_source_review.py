import copy
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from protocol_pdf_diff.formula_source_review import (
    FormulaSourceReview,
    prepare_formula_source_reviews,
    project_formula_source_review,
    render_source_page,
    section_scope_hash,
)
from protocol_pdf_diff.formula_source_review_export import render_formula_source_reviews
from protocol_pdf_diff.models import PageText, Section, SectionChange, SnippetPair


class FormulaSourceReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.sections = [
            Section("s", "1 Formula", "Formula", 1, ("1 Formula",), ("1",), 1, 1, t)
            for t in ("1 / M", "2 / M")
        ]
        sides = []
        context = []
        for index, s in enumerate(self.sections):
            import fitz

            pdf = self.d / f"source-{index}.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((80, 80), s.body)
            doc.save(pdf)
            doc.close()
            image = render_source_page(pdf, 1)
            from protocol_pdf_diff.formula_source_bridge import bridge
            roi = (0, 0, 595, 842)
            actual = bridge(pdf, 1, roi, s.body)
            context.append((str(pdf), hashlib.sha256(pdf.read_bytes()).hexdigest(), PageText(1, s.body)))
            if index == 0:
                self.pdf = pdf
            sides.append(
                {
                    "page": 1,
                    "section_id": "s",
                    "original_formula_text": s.body,
                    "parts": [
                        {
                            "text": s.body,
                            "offsets": list(range(len(s.body))),
                            "removed": [],
                            "page_offsets": [
                                i if not c.isspace() else None
                                for i, c in enumerate(s.body)
                            ],
                        }
                    ],
                    "bridge": actual,
                    "roi": roi,
                    "page_text_sha256": hashlib.sha256(s.body.encode()).hexdigest(),
                    "image_bytes": image,
                    "image_sha256": hashlib.sha256(image).hexdigest(),
                    "pdf_path": str(pdf),
                    "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                    "section_sha256": hashlib.sha256(s.body.encode()).hexdigest(),
                    "section_scope_sha256": section_scope_hash(s),
                }
            )
        self.context = tuple(context)
        self.receipt = FormulaSourceReview(*sides)
        self.change = SectionChange(
            "modified",
            *self.sections,
            0.9,
            replaced_snippets=[SnippetPair("1 / M", "2 / M")],
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_projection(self, r=None, c=None):
        return project_formula_source_review(
            c or self.change,
            prepare_formula_source_reviews([r or self.receipt], self.d, self.context),
        )

    def test_coherent_offsets_cannot_drift_from_actual_page(self):
        q = copy.deepcopy(self.receipt.old)
        for part in q["parts"]:
            part["page_offsets"] = [None if i is None else i + 1 for i in part["page_offsets"]]
        for glyph in q["bridge"]["glyphs"]:
            glyph["final_index"] += 1
        self.assertEqual(self.run_projection(replace(self.receipt, old=q)), self.change)

    def test_missing_actual_context_rejects(self):
        self.assertEqual(prepare_formula_source_reviews([self.receipt], self.d), ())

    def test_real_value_change_becomes_one_visible_unknown(self):
        c = self.run_projection()
        self.assertEqual(c.change_type, "review")
        self.assertEqual(len(c.formula_review_records), 1)
        self.assertEqual(
            c.formula_review_records[0]["old"]["original_formula_text"], "1 / M"
        )
        self.assertEqual(
            c.formula_review_records[0]["new"]["original_formula_text"], "2 / M"
        )
        self.assertEqual(len(self.change.replaced_snippets), 1)

    def test_neighbor_real_value_unchanged(self):
        c = replace(
            self.change,
            replaced_snippets=[
                *self.change.replaced_snippets,
                SnippetPair("Voltage shall be 800 mV.", "Voltage shall be 900 mV."),
            ],
        )
        out = self.run_projection(c=c)
        self.assertEqual(out.replaced_snippets, [c.replaced_snippets[1]])

    def test_missing_one_image(self):
        q = copy.deepcopy(self.receipt.new)
        q.pop("image_bytes")
        self.assertEqual(
            self.run_projection(r=replace(self.receipt, new=q)), self.change
        )

    def test_revoked_written_image(self):
        accepted = prepare_formula_source_reviews([self.receipt], self.d, self.context)
        Path(accepted[0][1][1]).unlink()
        self.assertEqual(
            project_formula_source_review(self.change, accepted), self.change
        )

    def test_changed_source_pdf(self):
        self.pdf.write_bytes(b"changed")
        self.assertEqual(self.run_projection(), self.change)

    def test_mixed_prose(self):
        q = copy.deepcopy(self.receipt.new)
        q["original_formula_text"] = "where 2 / M"
        self.assertEqual(
            self.run_projection(r=replace(self.receipt, new=q)), self.change
        )

    def test_scalar_conditions_cannot_transfer(self):
        for text in ["V <= 800 mV", "x >= 1", "V ≤ 900", "x = 1 if M", "x = 2 dB"]:
            q = copy.deepcopy(self.receipt.new)
            q["original_formula_text"] = text
            self.assertEqual(
                self.run_projection(r=replace(self.receipt, new=q)), self.change
            )

    def test_foreign_heading_same_body_is_rejected(self):
        changed = replace(
            self.change,
            new_section=replace(self.sections[1], heading_path=("foreign",)),
        )
        self.assertEqual(self.run_projection(c=changed), changed)

    def test_valid_other_page_png_is_rejected(self):
        q = copy.deepcopy(self.receipt.old)
        q.update(
            image_bytes=self.receipt.new["image_bytes"],
            image_sha256=self.receipt.new["image_sha256"],
        )
        self.assertEqual(
            self.run_projection(r=replace(self.receipt, old=q)), self.change
        )

    def test_original_formula_text_must_match_source_parts(self):
        q = copy.deepcopy(self.receipt.old)
        q["original_formula_text"] = "1 / N"
        self.assertEqual(
            self.run_projection(r=replace(self.receipt, old=q)), self.change
        )

    def test_gui_pending_formula_count(self):
        from protocol_pdf_diff.ui_shared import reported_reader_summary

        path = self.d / "report.md"
        path.write_text(
            "## 汇总\n| 正文差异候选（章节） | 0 |\n| 章节修改 / 新增 / 删除 | 0 / 0 / 0 |\n| 变化表格 | 0 |\n| 视觉漏检核对项 | 0 |\n| 正文待核实项（未分类） | 1 |\n| 公式来源待核实 | 1 |\n## Details\n"
        )
        summary = reported_reader_summary({"markdown": path})
        self.assertEqual(summary.pending_reviews, 1)
        self.assertEqual(summary.formula_reviews, 1)

    def test_duplicate_occurrence(self):
        c = replace(self.change, replaced_snippets=self.change.replaced_snippets * 2)
        self.assertEqual(self.run_projection(c=c), c)

    def test_wrong_section(self):
        c = replace(
            self.change, new_section=replace(self.sections[1], section_id="other")
        )
        self.assertEqual(self.run_projection(c=c), c)

    def test_missing_offset(self):
        q = copy.deepcopy(self.receipt.new)
        q["parts"][0]["page_offsets"][0] = None
        self.assertEqual(
            self.run_projection(r=replace(self.receipt, new=q)), self.change
        )

    def test_changed_raw_text(self):
        c = replace(self.change, replaced_snippets=[SnippetPair("1 / M", "3 / M")])
        self.assertEqual(self.run_projection(c=c), c)

    def test_bilateral_transaction(self):
        c = replace(
            self.change,
            replaced_snippets=[SnippetPair("1 / M", "ordinary requirement")],
        )
        self.assertEqual(self.run_projection(c=c), c)

    def test_all_exports_preserve_values_and_offsets(self):
        c = self.run_projection()
        parts = render_formula_source_reviews(c.formula_review_records, self.d)
        for part in parts:
            self.assertIn("1 / M", part)
            self.assertIn("2 / M", part)
            self.assertIn("待核实", part)
        csv = (self.d / "formula_source_reviews.csv").read_text()
        self.assertIn("1 / M", csv)
        self.assertIn("2 / M", csv)
        self.assertIn("page_offsets", csv)


if __name__ == "__main__":
    unittest.main()
