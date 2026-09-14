"""Offline behavioral regressions for physical-row evidence, no source PDFs."""

import base64
import csv
import io
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from PIL import Image
from protocol_pdf_diff.models import PhysicalTableRow, TableVisual, DocumentBlockKind
from protocol_pdf_diff.physical_table_rows import (
    render_physical_appendix,
    authorized_spans,
    write_physical_csv,
    verify_physical_csv,
)
from protocol_pdf_diff.visual_ownership import build_visual_owned_spans


class PhysicalTableReceiptTests(unittest.TestCase):
    def setUp(self):
        image = io.BytesIO()
        Image.new("RGB", (8, 8)).save(image, format="PNG")
        uri = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
        cells = ("Device\nValue", "C\nL\ns", "100\n120\n30", "fF\npH\nfF")
        words = tuple(
            tuple(
                (word, 1 + 25 * i, 1 + 10 * j, 20 + 25 * i, 8 + 10 * j)
                for j, word in enumerate(cell.split())
            )
            for i, cell in enumerate(cells)
        )
        self.row = PhysicalTableRow(
            "old",
            cells,
            (0, 0, 100, 100),
            tuple((i * 25, 0, (i + 1) * 25, 100) for i in range(4)),
            words,
        )
        self.old = TableVisual(
            1, 1, "Table 1", (0, 0, 100, 100), uri, [], "", physical_rows=(self.row,)
        )
        self.new = replace(
            self.old, page_number=2, physical_rows=(replace(self.row, row_id="new"),)
        )

    def render(self, old=None, new=None):
        return render_physical_appendix(
            [NS(old_tables=(old or self.old,), new_tables=(new or self.new,))]
        )

    def test_original_cells_newlines_images_and_collapsed_appendix(self):
        markup, payload, receipt = self.render()
        self.assertEqual(len(receipt), 2)
        self.assertEqual(markup.count("<pre "), 8)
        self.assertEqual(markup.count("<img"), 2)
        self.assertTrue(markup.startswith("<details "))
        self.assertNotIn("<details open", markup)
        self.assertIn("white-space:pre-wrap;overflow-wrap:anywhere", markup)
        self.assertEqual(markup.count('class="table-shot-page"'), 2)
        self.assertEqual(payload[0]["old"]["cells"], self.row.cells)
        self.assertFalse(self.old.row_alignment_reliable)

    def test_missing_or_invalid_image_has_no_receipt(self):
        for image in ("", "data:image/png;base64,YQ=="):
            with self.subTest(image=image):
                self.assertFalse(
                    self.render(new=replace(self.new, image_data_uri=image))[2]
                )

    def test_missing_cell_or_word_has_no_receipt(self):
        for row in (
            replace(self.row, cell_words=()),
            replace(self.row, cells=self.row.cells[:3]),
        ):
            with self.subTest(row=row):
                self.assertFalse(
                    self.render(new=replace(self.new, physical_rows=(row,)))[2]
                )

    def test_same_bilateral_reordering_cannot_self_certify(self):
        forged = replace(
            self.row, cells=(*self.row.cells[:2], "120\n100\n30", self.row.cells[3])
        )
        self.assertFalse(
            self.render(
                replace(self.old, physical_rows=(forged,)),
                replace(self.new, physical_rows=(replace(forged, row_id="new"),)),
            )[2]
        )

    def test_coherent_source_numeric_change_is_not_deduplicated(self):
        row = replace(
            self.new.physical_rows[0],
            cells=(*self.row.cells[:2], "101\n120\n30", self.row.cells[3]),
            cell_words=(
                *self.row.cell_words[:2],
                tuple(
                    ("101" if w[0] == "100" else w[0], *w[1:])
                    for w in self.row.cell_words[2]
                ),
                self.row.cell_words[3],
            ),
        )
        self.assertFalse(self.render(new=replace(self.new, physical_rows=(row,)))[2])

    def test_duplicate_correspondence_has_no_receipt(self):
        self.assertFalse(
            self.render(
                new=replace(self.new, physical_rows=self.new.physical_rows * 2)
            )[2]
        )

    def test_same_table_image_is_emitted_once_for_multiple_physical_rows(self):
        words = list(self.row.cell_words)
        words[0] = tuple(
            ("Other" if word[0] == "Device" else word[0], *word[1:])
            for word in words[0]
        )
        second_old = replace(
            self.row,
            row_id="old-2",
            cells=("Other\nValue", *self.row.cells[1:]),
            cell_words=tuple(words),
        )
        second_new = replace(second_old, row_id="new-2")
        old_table = replace(self.old, physical_rows=(self.row, second_old))
        new_table = replace(self.new, physical_rows=(self.new.physical_rows[0], second_new))

        markup, _payload, receipt = self.render(old=old_table, new=new_table)

        self.assertEqual(4, len(receipt))
        self.assertEqual(2, markup.count("<img"))
        self.assertEqual(2, markup.count("table-shot-reused"))

    def test_report_level_table_image_can_be_reused_without_embedding(self):
        markup, _payload, receipt = render_physical_appendix(
            [NS(old_tables=(self.old,), new_tables=(self.new,))],
            displayed_source_keys={("old", 1, 1), ("new", 2, 1)},
        )
        self.assertEqual(2, len(receipt))
        self.assertNotIn("<img", markup)
        self.assertEqual(2, markup.count("上方表格证据"))

    def test_candidate_is_inert_without_authorization(self):
        candidates = {"physical:old:old:S": {"Device": [(0, 6)]}}
        self.assertEqual(authorized_spans(candidates, "old", "S", set()), {})
        self.assertEqual(
            authorized_spans(candidates, "old", "S", {("old", "old")}),
            {"Device": [(0, 6)]},
        )

    def test_csv_preserves_cells_and_missing_or_tampered_csv_withholds_authorization(
        self,
    ):
        _, payload, receipt = self.render()
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "physical_table_records.csv"
            self.assertEqual(write_physical_csv(payload, path), receipt)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[1][3:7], list(self.row.cells))
            expected = [r[:] for r in rows[1:]]
            rows[1][5] = "101\n120\n30"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                csv.writer(stream).writerows(rows)
            self.assertFalse(verify_physical_csv(path, rows[0], expected))
            path.unlink()
            self.assertFalse(verify_physical_csv(path, rows[0], expected))

    def test_report_missing_csv_acceptance_keeps_body(self):
        import json
        from unittest.mock import patch
        from protocol_pdf_diff.models import (
            DiffResult,
            DiffOptions,
            Section,
            SectionChange,
        )
        from protocol_pdf_diff import reporting

        old_section = Section(
            "S",
            "1 Parameters",
            "Parameters",
            1,
            ("1 Parameters",),
            ("1",),
            1,
            1,
            "Device",
        )
        new_section = replace(old_section, start_page=2, end_page=2, body="Other")
        result = DiffResult(
            Path("old.pdf"),
            Path("new.pdf"),
            [old_section],
            [new_section],
            [
                SectionChange(
                    "modified",
                    old_section,
                    new_section,
                    0.9,
                    removed_snippets=["Device"],
                )
            ],
            [],
            old_table_visuals=[self.old],
            new_table_visuals=[self.new],
            visual_owned_spans={"physical:old:old:S": {"Device": [(0, 6)]}},
        )
        groups = [reporting._TableVisualGroup((self.old,), (self.new,))]
        with (
            TemporaryDirectory() as tmp,
            patch.object(reporting, "_paired_table_visuals", return_value=groups),
            patch(
                "protocol_pdf_diff.physical_table_rows.write_physical_csv",
                return_value=set(),
            ),
        ):
            paths = reporting.write_reports(result, tmp, DiffOptions())
            data = json.loads(paths["json"].read_text())
            self.assertEqual(data["physical_table_dedup_authorizations"], [])
            self.assertTrue(
                any("Device" in str(change) for change in data["content_changes"])
            )
            self.assertFalse(paths["physical_table_csv"].exists())

    def test_plain_export_preserves_special_characters_and_cell_boundaries(self):
        from protocol_pdf_diff.physical_table_rows import physical_text_export

        cells = ("A < B & C\nnext", "x```y", "100\n120", "mV")
        payload = [{side: {"page": 1, "cells": cells} for side in ("old", "new")}]
        plain = physical_text_export(payload)
        markdown = physical_text_export(payload, markdown=True)
        for cell in cells:
            self.assertIn(cell, plain)
            self.assertIn(cell, markdown)
        self.assertIn("````\nx```y\n````", markdown)
        self.assertNotIn("&lt;", plain)

    def test_report_text_has_original_cells_without_html_or_image_payload(self):
        from unittest.mock import patch
        from protocol_pdf_diff import reporting
        from protocol_pdf_diff.models import DiffResult, DiffOptions

        result = DiffResult(
            Path("old.pdf"),
            Path("new.pdf"),
            [],
            [],
            [],
            [],
            old_table_visuals=[self.old],
            new_table_visuals=[self.new],
        )
        groups = [reporting._TableVisualGroup((self.old,), (self.new,))]
        with (
            TemporaryDirectory() as tmp,
            patch.object(reporting, "_paired_table_visuals", return_value=groups),
        ):
            paths = reporting.write_reports(result, tmp, DiffOptions())
            plain = paths["text"].read_text()
            markdown = paths["markdown"].read_text()
            for cell in self.row.cells:
                self.assertIn(cell, plain)
                self.assertIn(cell, markdown)
            self.assertNotIn("<pre", plain)
            self.assertNotIn("<td", plain)
            self.assertNotIn("base64,", plain)
            self.assertNotIn("base64,", markdown)


class PhysicalTableOccurrenceTests(unittest.TestCase):
    def setUp(self):
        self.words = (
            ("Device", 1, 1, 6, 8),
            ("die", 7, 1, 10, 8),
            ("model", 11, 1, 18, 8),
        )
        self.section = NS(start_page=1, end_page=1, section_id="S")
        self.change = NS(
            old_section=self.section,
            new_section=None,
            removed_snippets=["Device die model"],
            added_snippets=[],
            audit_removed_snippets=None,
            audit_added_snippets=None,
            audit_replaced_snippets=None,
            replaced_snippets=[],
        )
        self.result = NS(
            old_table_visuals=[], new_table_visuals=[], changes=[self.change]
        )

    def page(self, number=1, words=None, text="Device die model", page_text=None):
        block = NS(
            kind=DocumentBlockKind.TEXT,
            text=text,
            word_boxes=self.words if words is None else words,
            reading_order=0,
        )
        return NS(
            page_number=number,
            ocr_used=False,
            blocks=[block],
            text=page_text or text,
            visual_noise_bboxes=(),
        )

    def run_owner(self, pages):
        return build_visual_owned_spans(
            self.result,
            NS(pages=pages),
            NS(pages=[]),
            (),
            _physical=("old", 1, (0, 0, 20, 10)),
        )

    def test_unique_source_occurrence(self):
        self.assertEqual(
            self.run_owner([self.page()]), {"old:S": {"Device die model": [(0, 16)]}}
        )

    def test_missing_or_reordered_words_veto(self):
        for words in (self.words[:-1], (self.words[1], self.words[0], self.words[2])):
            with self.subTest(words=words):
                self.assertFalse(self.run_owner([self.page(words=words)]))

    def test_missing_geometry_duplicate_veto(self):
        self.assertFalse(
            self.run_owner([self.page(page_text="Device die model Device die model")])
        )

    def test_other_page_duplicate_still_vetoes(self):
        self.section.end_page = 2
        self.assertFalse(self.run_owner([self.page(), self.page(2)]))

    def test_unrelated_changes_are_not_consumed(self):
        class Unrelated:
            old_section = NS(start_page=9, end_page=10, section_id="other")
            new_section = None

            def __getattr__(self, name):
                raise AssertionError("unrelated snippets consumed: " + name)

        self.result.changes.append(Unrelated())
        self.assertTrue(self.run_owner([self.page()]))


if __name__ == "__main__":
    unittest.main()
