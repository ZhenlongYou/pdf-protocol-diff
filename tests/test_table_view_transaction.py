import copy
import io
import os
import pickle
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from protocol_pdf_diff import table_view_transaction as t


class RowEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rows = [["Parameter", "Setting", "Units"], ["gain", "[0 1]", ""]]
        self.boxes = [
            [(0, 0, 100, 10), (100, 0, 200, 10), (200, 0, 300, 10)],
            [(0, 10, 100, 20), (100, 10, 200, 20), (200, 10, 300, 20)],
        ]
        self.table = SimpleNamespace(
            rows=[
                SimpleNamespace(cells=b, bbox=(0, i * 10, 300, (i + 1) * 10))
                for i, b in enumerate(self.boxes)
            ]
        )
        chars = []
        self.words = []
        for i, row in enumerate(self.rows):
            observed = []
            for j, value in enumerate(row):
                observed.append(
                    [
                        {
                            "text": value,
                            "x0": j * 100 + 1,
                            "x1": j * 100 + len(value) + 2,
                            "top": i * 10 + 1,
                            "bottom": i * 10 + 8,
                            "size": 7,
                        }
                    ]
                    if value
                    else []
                )
                for k, c in enumerate(value):
                    if c.isspace():
                        continue
                    n = len(chars)
                    chars.append(
                        (
                            n,
                            c,
                            j * 100 + 1 + k,
                            i * 10 + 1,
                            j * 100 + 2 + k,
                            i * 10 + 8,
                            n,
                            1,
                        )
                    )
            self.words.append(observed)
        self.page = SimpleNamespace(
            page_number=1, _physical_native_evidence=("text", tuple(chars))
        )

    def captured(self):
        state = {"rows": [], "pages": {}, "current_name": "old.pdf"}
        token = t._ACTIVE.set(state)
        try:
            t.capture_rows(self.page, self.table, self.rows, self.words, False)
        finally:
            t._ACTIVE.reset(token)
        return state["rows"]

    def test_complete_row_with_empty_units(self):
        self.assertEqual(len(self.captured()), 1)

    def test_extra_uncovered_800(self):
        self.rows[1][1] += " | Voltage 800 mV"
        self.assertEqual(self.captured(), [])

    def test_changed_value(self):
        self.rows[1][1] = "[0 2]"
        self.assertEqual(self.captured(), [])

    def test_wrong_identity(self):
        self.rows[1][0] = "loss"
        self.assertEqual(self.captured(), [])

    def test_mixed_prose(self):
        self.rows[1][1] += "\nordinary text"
        self.assertEqual(self.captured(), [])

    def test_unknown_header(self):
        self.rows[0][0] = "Unknown"
        self.assertEqual(self.captured(), [])

    def test_missing_glyph(self):
        text, c = self.page._physical_native_evidence
        self.page._physical_native_evidence = (text, c[:-1])
        self.assertEqual(self.captured(), [])

    def test_extra_glyph(self):
        text, c = self.page._physical_native_evidence
        x = list(c[-1])
        x[1] = "8"
        x[0] = 999
        x[6] = 999
        self.page._physical_native_evidence = (text, c + (tuple(x),))
        self.assertEqual(self.captured(), [])

    def test_no_native(self):
        self.page._physical_native_evidence = (None, ())
        self.assertEqual(self.captured(), [])

    def test_overlapping_bad_row(self):
        self.table.rows.append(
            SimpleNamespace(cells=self.boxes[1], bbox=(0, 10, 300, 20))
        )
        self.rows.append(["bad", "bad", "bad"])
        self.words.append([[], [], []])
        self.assertEqual(self.captured(), [])


class ContractTests(unittest.TestCase):
    def test_injected_writer_gets_original_and_dict_contract(self):
        original = object()
        candidate = object()
        bundle = t.DualResult(original, candidate, [], "pending")
        calls = []

        def writer(result, directory, options):
            calls.append(result)
            return {"html": Path("original.html")}

        outcome = t.report_outcome(bundle, ".", None, writer=writer)
        self.assertEqual(calls, [original])
        self.assertIs(outcome.selected_result, original)
        self.assertEqual(outcome.outputs, {"html": Path("original.html")})

    def test_ordinary_writer_gets_unwrapped_result(self):
        original = object()
        seen = []
        outcome = t.report_outcome(
            original, ".", None, writer=lambda r, *a: seen.append(r) or {}
        )
        self.assertIs(outcome.selected_result, original)
        self.assertEqual(seen, [original])


class FinalReceiptTests(unittest.TestCase):
    def setUp(self):
        import base64
        from dataclasses import replace

        from protocol_pdf_diff.models import DiffOptions, DiffResult, TableVisual

        fixture = RowEvidenceTests()
        fixture.setUp()
        row = fixture.captured()[0]
        buf = io.BytesIO()
        Image.new("RGB", (5, 5)).save(buf, format="PNG")
        uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        old = TableVisual(
            1,
            1,
            "Table 1",
            tuple(row["bbox"]),
            uri,
            [],
            "",
            raw_source_cells=(tuple(row["cells"]),),
            raw_cell_bounds=(tuple(row["cell_bboxes"]),),
        )
        new = replace(old, page_number=2)
        original = DiffResult(
            Path("old.pdf"),
            Path("new.pdf"),
            [],
            [],
            [],
            [],
            old_table_visuals=[old],
            new_table_visuals=[new],
        )
        self.bundle = t.DualResult(
            original,
            copy.deepcopy(original),
            [
                dict(row, name="old.pdf", page=1),
                dict(row, name="new.pdf", page=2, proposed=False),
            ],
            "pending",
        )
        self.options = DiffOptions()

    def test_real_reports_success_and_original_immutable(self):
        before = pickle.dumps(self.bundle.original)
        with tempfile.TemporaryDirectory() as directory:
            out = t.write_reports_transaction(self.bundle, directory, self.options)
            self.assertIs(out.selected_result, self.bundle.candidate)
            self.assertTrue(
                (out.outputs["json"].parent / "three_cell_records.csv").exists()
            )
            self.assertIn("three_cell_records", out.outputs["json"].read_text())
        self.assertEqual(pickle.dumps(self.bundle.original), before)

    def test_missing_image_whole_fallback(self):
        from dataclasses import replace

        self.bundle.original = replace(
            self.bundle.original,
            old_table_visuals=[
                replace(self.bundle.original.old_table_visuals[0], image_data_uri="")
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            out = t.write_reports_transaction(self.bundle, directory, self.options)
            self.assertIs(out.selected_result, self.bundle.original)
            self.assertFalse(
                (out.outputs["json"].parent / "three_cell_records.csv").exists()
            )

    def test_missing_csv_whole_fallback(self):
        original_open = Path.open

        def opened(path, *args, **kwargs):
            if path.name == "three_cell_records.csv":
                raise OSError("missing CSV")
            return original_open(path, *args, **kwargs)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(Path, "open", opened),
        ):
            out = t.write_reports_transaction(self.bundle, directory, self.options)
            self.assertIs(out.selected_result, self.bundle.original)

    def test_final_copy_failure_publishes_clean_fallback(self):
        original_write = Path.write_bytes

        def written(path, *args, **kwargs):
            if path.name == "three_cell_records.csv":
                raise OSError("missing final CSV")
            return original_write(path, *args, **kwargs)

        before = pickle.dumps(self.bundle.original)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(Path, "write_bytes", written),
        ):
            out = t.write_reports_transaction(self.bundle, directory, self.options)
            self.assertIs(out.selected_result, self.bundle.original)
            self.assertEqual(
                out.outputs["report_dir"].parent.resolve(), Path(directory).resolve()
            )
            self.assertFalse(
                (out.outputs["json"].parent / "three_cell_records.csv").exists()
            )
            self.assertNotIn("three_cell_records", out.outputs["json"].read_text())
        self.assertEqual(pickle.dumps(self.bundle.original), before)

    def test_single_side_value_change_refuses_projection(self):
        self.bundle.rows[1] = dict(self.bundle.rows[1], cells=["gain", "[0 2]", ""])
        with tempfile.TemporaryDirectory() as directory:
            out = t.write_reports_transaction(self.bundle, directory, self.options)
            self.assertIs(out.selected_result, self.bundle.original)


class GroupIdentityTests(unittest.TestCase):
    setUp = FinalReceiptTests.setUp

    def test_cross_group_same_name_cannot_borrow(self):
        a = self.bundle.original.old_table_visuals[0]
        b = self.bundle.original.new_table_visuals[0]
        groups = [
            SimpleNamespace(old_tables=[a], new_tables=[]),
            SimpleNamespace(old_tables=[], new_tables=[b]),
        ]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "protocol_pdf_diff.reporting._paired_table_visuals", return_value=groups
            ),
        ):
            self.assertFalse(t._write_receipts(self.bundle, Path(directory)))

    def test_same_name_in_other_group_does_not_confuse_owner(self):
        from dataclasses import replace

        a = self.bundle.original.old_table_visuals[0]
        b = self.bundle.original.new_table_visuals[0]
        other = replace(a, page_number=3)
        self.bundle.rows.append(dict(self.bundle.rows[0], page=3, proposed=False))
        groups = [
            SimpleNamespace(old_tables=[a], new_tables=[b]),
            SimpleNamespace(old_tables=[other], new_tables=[]),
        ]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "protocol_pdf_diff.reporting._paired_table_visuals", return_value=groups
            ),
        ):
            self.assertTrue(t._write_receipts(self.bundle, Path(directory)))


class SourceGeometryTests(unittest.TestCase):
    setUp = FinalReceiptTests.setUp

    def test_whole_receipt_shift_cannot_borrow_image(self):
        row = self.bundle.rows[0]
        row["bbox"] = tuple(v + 100 if i % 2 else v for i, v in enumerate(row["bbox"]))
        row["cell_bboxes"] = [
            tuple(v + 100 if i % 2 else v for i, v in enumerate(b))
            for b in row["cell_bboxes"]
        ]
        row["native_chars"] = [
            (n, c, x0, y0 + 100, x1, y1 + 100, g, k)
            for n, c, x0, y0, x1, y1, g, k in row["native_chars"]
        ]
        with tempfile.TemporaryDirectory() as d:
            out = t.write_reports_transaction(self.bundle, d, self.options)
            self.assertIs(out.selected_result, self.bundle.original)

    def test_missing_source_cell_geometry_rejects(self):
        from dataclasses import replace

        self.bundle.original = replace(
            self.bundle.original,
            old_table_visuals=[
                replace(self.bundle.original.old_table_visuals[0], raw_cell_bounds=())
            ],
        )
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(t._write_receipts(self.bundle, Path(d)))

    def test_image_extent_excludes_real_row(self):
        from dataclasses import replace

        self.bundle.original = replace(
            self.bundle.original,
            old_table_visuals=[
                replace(self.bundle.original.old_table_visuals[0], bbox=(0, 0, 300, 5))
            ],
        )
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(t._write_receipts(self.bundle, Path(d)))

    def test_reordered_native_source_rejects(self):
        self.bundle.rows[0]["native_chars"] = list(
            reversed(self.bundle.rows[0]["native_chars"])
        )
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(t._write_receipts(self.bundle, Path(d)))


class ExclusivePublicationTests(unittest.TestCase):
    setUp = FinalReceiptTests.setUp

    def test_fixed_clock_failure_preserves_existing_user_directory(self):
        from datetime import datetime

        from protocol_pdf_diff import reporting

        class Clock:
            @staticmethod
            def now():
                return datetime(2026, 9, 14, 8, 0, 0)  # noqa: DTZ001 -- Mirror the writer's local clock.

        original = Path.write_bytes

        def write(path, *args, **kwargs):
            if path.name == "three_cell_records.csv":
                raise OSError("final copy fault")
            return original(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "protocol_diff_20260914_080000"
            existing.mkdir()
            sentinel = existing / "user-existing-important.txt"
            sentinel.write_text("must survive")
            with (
                patch.object(reporting, "datetime", Clock),
                patch.object(Path, "write_bytes", write),
            ):
                out = t.write_reports_transaction(self.bundle, directory, self.options)
            self.assertEqual(sentinel.read_text(), "must survive")
            self.assertIs(out.selected_result, self.bundle.original)
            self.assertNotEqual(out.outputs["report_dir"], existing)
            self.assertEqual(
                out.outputs["report_dir"].parent, Path(directory).resolve()
            )
            self.assertFalse(
                any(
                    p.name.startswith(".table-view-owned-")
                    for p in Path(directory).iterdir()
                )
            )

    def test_repeated_reports_get_unique_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            a = t.write_reports_transaction(self.bundle, directory, self.options)
            b = t.write_reports_transaction(self.bundle, directory, self.options)
            self.assertNotEqual(a.outputs["report_dir"], b.outputs["report_dir"])
            self.assertTrue(a.outputs["html"].exists())
            self.assertTrue(b.outputs["html"].exists())

    def test_publication_works_when_windows_rejects_existing_directory(self):
        """The destination must be absent before a cross-platform directory rename."""

        real_replace = os.replace
        destination_existed = []

        def windows_directory_replace(source, destination):
            existed = Path(destination).exists()
            destination_existed.append(existed)
            if existed:
                raise FileExistsError(183, "destination already exists")
            return real_replace(source, destination)

        with tempfile.TemporaryDirectory() as directory, patch(
            "os.replace", side_effect=windows_directory_replace
        ):
            outcome = t.write_reports_transaction(self.bundle, directory, self.options)
            self.assertTrue(outcome.outputs["html"].is_file())

        self.assertEqual([False], destination_existed)

    def test_publish_failure_only_removes_owned_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "user.txt"
            marker.write_text("keep")
            with patch("os.replace", side_effect=OSError("publication failed")), self.assertRaises(OSError):
                t.write_reports_transaction(self.bundle, directory, self.options)
            self.assertEqual(list(Path(directory).iterdir()), [marker])


if __name__ == "__main__":
    unittest.main()
