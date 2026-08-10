"""Accuracy regressions for table-structure review findings in reader reports."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from protocol_pdf_diff import reporting
from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import (
    DiffOptions,
    DiffResult,
    ExtractionResult,
    PageText,
    Section,
    TableChange,
    TableRowChange,
    TableVisual,
)


class ReportingReviewAccuracyTests(unittest.TestCase):
    """Uncertain table structure must stay visible without becoming a false change."""

    @staticmethod
    def _table(*, status: str = "", value: str = "10") -> TableVisual:
        return TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1. Stable limits",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[f"表格行: T1 | Parameter=Limit | Value={value}"],
            grid_summary="structured rows",
            ocr_status=status,
        )

    def test_structure_review_row_is_never_hidden_by_html_row_cap(self) -> None:
        """Twenty-one real deltas cannot push the manual-review evidence off screen."""

        rows = tuple(
            TableRowChange(f"P{index}", str(index), str(index + 1), "替换/修改")
            for index in range(21)
        ) + (
            TableRowChange(
                "表格结构复核",
                "扁平文字匹配；行列边界未验证",
                "结构化表格",
                "需人工复核",
            ),
        )
        change = TableChange(
            change_type="modified",
            old_tables=(self._table(),),
            new_tables=(self._table(value="11"),),
            similarity=1.0,
            caption_changed=False,
            row_changes=rows,
        )

        html = reporting._render_table_row_summary(change)

        self.assertIn("表格结构复核", html)
        self.assertIn("需人工复核", html)
        self.assertIn("另有 2 行表格变化未展示", html)

    def test_material_row_is_never_hidden_by_many_review_rows(self) -> None:
        """Twenty uncertainty rows cannot hide the only confirmed limit change."""

        rows = tuple(
            TableRowChange(
                f"复核项 {index}",
                "归属未验证",
                "归属未验证",
                "需人工复核",
            )
            for index in range(20)
        ) + (
            TableRowChange(
                "CRITICAL_LIMIT",
                "1 dB",
                "3 dB",
                "实质变化",
            ),
        )
        change = TableChange(
            change_type="modified",
            old_tables=(self._table(),),
            new_tables=(self._table(value="11"),),
            similarity=1.0,
            caption_changed=False,
            row_changes=rows,
        )

        html = reporting._render_table_row_summary(change)

        self.assertIn("CRITICAL_LIMIT", html)
        self.assertIn('<mark class="del">1</mark> dB', html)
        self.assertIn('<mark class="ins">3</mark> dB', html)
        self.assertIn("需人工复核", html)
        self.assertIn("另有 1 行表格变化未展示", html)

    def test_structure_review_describes_empty_and_mixed_sides_truthfully(self) -> None:
        """An absent side is not structured, and a mixed side is not wholly flat."""

        text_backed = self._table(status="text_backed_exact_match")
        structured = self._table()
        deleted = reporting._text_backed_table_structure_review_change(
            reporting._TableVisualGroup((text_backed,), ())
        )
        mixed = reporting._text_backed_table_structure_review_change(
            reporting._TableVisualGroup((text_backed, structured), (structured,))
        )
        both_flat = reporting._text_backed_table_structure_review_change(
            reporting._TableVisualGroup((text_backed,), (text_backed,))
        )

        self.assertIsNotNone(deleted)
        self.assertEqual("无对应表格", deleted.new_value)
        self.assertIsNotNone(mixed)
        self.assertIn("含扁平文字匹配", mixed.old_value)
        self.assertEqual("结构化表格", mixed.new_value)
        self.assertIsNotNone(both_flat)

        shot_html = reporting._render_one_table_shot_page(text_backed)
        self.assertIn("扁平文字精确匹配，行列边界未验证", shot_html)
        self.assertIn("行列网格：未验证", shot_html)
        self.assertNotIn("仅使用结构化表格行摘要", shot_html)

    def test_review_only_table_is_not_reported_as_a_confirmed_modification(self) -> None:
        """Equal contents with one flat reconstruction create a review, not a change."""

        body = "The stable limit shall remain traceable to the source table. " * 10
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old.pdf"),
                pages=[PageText(1, f"1 Scope\n{body}")],
                table_visuals=[self._table(status="text_backed_exact_match")],
            ),
            ExtractionResult(
                pdf_path=Path("new.pdf"),
                pages=[PageText(1, f"1 Scope\n{body}")],
                table_visuals=[self._table()],
            ),
            DiffOptions(),
        )
        changes = reporting._build_table_changes(result)

        self.assertEqual(1, len(changes))
        self.assertEqual("review", changes[0].change_type)
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = reporting.write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            html = outputs["html"].read_text(encoding="utf-8")
            table_csv = outputs["table_csv"].read_text(encoding="utf-8-sig")

        table_payload = payload["table_changes"][0]
        self.assertEqual(0, table_payload["row_change_count"])
        self.assertEqual(1, table_payload["review_count"])
        self.assertIn("<strong>0</strong><span>变化表格</span>", html)
        self.assertIn("<strong>1</strong><span>表格复核项</span>", html)
        self.assertNotIn("表格结构复核", html)
        self.assertIn("表格行列结构", html)
        self.assertIn("行列边界未验证", html)
        self.assertIn("表格补充证据（变化与复核）", html)
        self.assertIn("表格结构复核", table_csv)
        self.assertIn("需人工复核", table_csv)

    def test_identical_snapshot_window_has_no_table_review_cards(self) -> None:
        """Identical source bytes cannot contain a semantic table difference."""

        body = "The stable protocol requirement remains unchanged. " * 12
        rows = [
            "表格行: T1 | Parameter=A | Value=1 | Units=UI",
            "表格行: T1 | Parameter=B | Value=2 | Units=dB",
        ]
        table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1. Stable limits",
            bbox=(10.0, 10.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=rows,
            grid_summary="grid",
            content_fully_represented=True,
            row_alignment_reliable=False,
        )
        source_sha256 = "a" * 64
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-copy.pdf"),
                pages=[PageText(1, f"1 Scope\n{body}")],
                total_pages=1,
                selected_start_page=1,
                selected_end_page=1,
                table_visuals=[table],
                source_sha256=source_sha256,
            ),
            ExtractionResult(
                pdf_path=Path("new-copy.pdf"),
                pages=[PageText(1, f"1 Scope\n{body}")],
                total_pages=1,
                selected_start_page=1,
                selected_end_page=1,
                table_visuals=[TableVisual(**table.__dict__)],
                source_sha256=source_sha256,
            ),
            DiffOptions(visual_watchdog=False),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = reporting.write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            html = outputs["html"].read_text(encoding="utf-8")

        self.assertEqual([], payload["changes"])
        self.assertEqual([], payload["table_changes"])
        self.assertEqual([], payload["formula_changes"])
        self.assertIn("<strong>0</strong><span>表格复核项</span>", html)
        self.assertNotIn("表格行归属", html)

        different_window_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("same-source.pdf"),
                pages=[PageText(1, f"1 Scope\n{body}")],
                total_pages=2,
                selected_start_page=1,
                selected_end_page=1,
                table_visuals=[table],
                source_sha256=source_sha256,
            ),
            ExtractionResult(
                pdf_path=Path("same-source.pdf"),
                pages=[PageText(2, f"1 Scope\n{body}")],
                total_pages=2,
                selected_start_page=2,
                selected_end_page=2,
                table_visuals=[TableVisual(**{**table.__dict__, "page_number": 2})],
                source_sha256=source_sha256,
            ),
            DiffOptions(visual_watchdog=False),
        )
        self.assertEqual(1, len(reporting._build_table_changes(different_window_result)))

    def test_unchanged_lossless_multirow_table_keeps_compact_alignment_review(self) -> None:
        """A reader card must exist before uncertain row text can replace a body wall."""

        rows = [
            "表格行: T1 | Parameter=A | Value=1 | Units=UI",
            "表格行: T1 | Parameter=B | Value=2 | Units=dB",
        ]
        old_table = TableVisual(
            1,
            1,
            "Table 1. Limits",
            (10.0, 10.0, 100.0, 100.0),
            "",
            rows,
            "grid",
            content_fully_represented=True,
            row_alignment_reliable=False,
        )
        new_table = TableVisual(**old_table.__dict__)
        result = DiffResult(
            Path("old.pdf"),
            Path("new.pdf"),
            [],
            [],
            [],
            [],
            old_table_visuals=[old_table],
            new_table_visuals=[new_table],
        )

        changes = reporting._build_table_changes(result)

        self.assertEqual(1, len(changes))
        self.assertEqual("review", changes[0].change_type)
        self.assertEqual("表格行归属", changes[0].row_changes[0].item)
        self.assertEqual("需人工复核", changes[0].row_changes[0].change_type)

    def test_reader_hides_generic_structure_row_but_keeps_real_table_fact(self) -> None:
        """A mixed table card remains useful after its generic reminder is removed."""

        real_row = TableRowChange("Target BER", "1e-6", "1e-7", "替换/修改")
        generic_row = TableRowChange(
            "表格结构复核",
            "扁平文字匹配；行列边界未验证",
            "结构化表格",
            "需人工复核",
        )
        change = TableChange(
            change_type="modified",
            old_tables=(self._table(status="text_backed_exact_match"),),
            new_tables=(self._table(value="11"),),
            similarity=0.99,
            caption_changed=False,
            row_changes=(real_row, generic_row),
        )

        reader_changes = reporting._reader_table_changes([change])

        self.assertEqual(1, len(reader_changes))
        self.assertEqual((real_row,), reader_changes[0].row_changes)

        empty_result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(reporting, "_build_table_changes", return_value=[change]):
                outputs = reporting.write_reports(empty_result, temp_dir, DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            markdown = outputs["markdown"].read_text(encoding="utf-8")
            text_report = outputs["text"].read_text(encoding="utf-8")
            table_csv = outputs["table_csv"].read_text(encoding="utf-8-sig")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        for rendered in (html, markdown, text_report):
            self.assertIn("Target BER", rendered)
            self.assertNotIn("表格结构复核", rendered)
        self.assertIn("Target BER", table_csv)
        self.assertIn("表格结构复核", table_csv)
        self.assertEqual(2, len(payload["table_changes"][0]["row_changes"]))

    def test_unreliable_multirow_alignment_cannot_confirm_row_value_swaps(self) -> None:
        """Character coverage without row pairing evidence yields review-only facts."""

        old_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1. Limits",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[
                "表格行: T1 | Parameter=A | Value=1",
                "表格行: T1 | Parameter=B | Value=2",
            ],
            grid_summary="structured rows",
            content_fully_represented=True,
            row_alignment_reliable=False,
        )
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "row_texts": [
                    "表格行: T1 | Parameter=A | Value=2",
                    "表格行: T1 | Parameter=B | Value=1",
                ],
            }
        )
        result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_table_visuals=[old_table],
            new_table_visuals=[new_table],
        )

        changes = reporting._build_table_changes(result)

        self.assertEqual(1, len(changes))
        self.assertEqual("review", changes[0].change_type)
        self.assertTrue(changes[0].row_changes)
        self.assertTrue(
            all(row.change_type == "需人工复核" for row in changes[0].row_changes)
        )

        aggregated_old = TableVisual(
            **{
                **old_table.__dict__,
                "row_texts": [
                    r"表格行: T1 | Parameter=A\nB | Value=1\n2 | Units=UI\nUI"
                ],
                "content_fully_represented": False,
            }
        )
        expanded_new = TableVisual(
            **{
                **new_table.__dict__,
                "row_texts": [
                    "表格行: T1 | Parameter=A | Value=1 | Units=UI",
                    "表格行: T1 | Parameter=B | Value=2 | Units=UI",
                ],
                "content_fully_represented": False,
                "row_alignment_reliable": True,
            }
        )
        aggregate_result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_table_visuals=[aggregated_old],
            new_table_visuals=[expanded_new],
        )
        aggregate_changes = reporting._build_table_changes(aggregate_result)

        self.assertEqual(1, len(aggregate_changes))
        self.assertEqual("review", aggregate_changes[0].change_type)
        self.assertTrue(
            all(
                row.change_type == "需人工复核"
                for row in aggregate_changes[0].row_changes
            )
        )

    def test_unnumbered_revision_records_pair_by_context_and_field_schema(self) -> None:
        """One added revision is one row change, not separate deleted/added tables."""

        old_table = TableVisual(
            page_number=3,
            table_number=1,
            title="in the table below:",
            bbox=(10.0, 20.0, 500.0, 160.0),
            image_data_uri="",
            row_texts=[
                "表格行: T1 | Revision=1.0 | Date=1 Jan 2026 | Description=Baseline"
            ],
            grid_summary="structured rows",
            row_alignment_reliable=True,
        )
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "row_texts": [
                    *old_table.row_texts,
                    "表格行: T1 | Revision=1.1 | Date=2 Feb 2026 | Description=Update",
                ],
            }
        )
        result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_table_visuals=[old_table],
            new_table_visuals=[new_table],
        )

        changes = reporting._build_table_changes(result)

        self.assertEqual(1, len(changes))
        self.assertEqual("modified", changes[0].change_type)
        self.assertEqual(1, len(changes[0].row_changes))
        self.assertEqual("新表新增行", changes[0].row_changes[0].change_type)

    def test_revision_record_identity_survives_reworded_intro_and_multiple_added_rows(self) -> None:
        """A rewritten revision caption cannot split one record into delete plus add."""

        old_table = TableVisual(
            page_number=3,
            table_number=1,
            title="Changes from the previous revision are listed in the table below:",
            bbox=(10.0, 20.0, 500.0, 160.0),
            image_data_uri="",
            row_texts=[
                "表格行: T1 | Revision=1.0 | Date=1 Jan 2026 | Description=Baseline"
            ],
            grid_summary="structured rows",
            row_alignment_reliable=True,
        )
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "title": "Revision History",
                "row_texts": [
                    *old_table.row_texts,
                    "表格行: T1 | Revision=1.1 | Date=2 Feb 2026 | Description=Editorial",
                    "表格行: T1 | Revision=1.2 | Date=3 Mar 2026 | Description=Technical",
                ],
            }
        )
        result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_table_visuals=[old_table],
            new_table_visuals=[new_table],
        )

        changes = reporting._build_table_changes(result)

        self.assertEqual(1, len(changes))
        self.assertEqual("modified", changes[0].change_type)
        self.assertTrue(changes[0].old_tables)
        self.assertTrue(changes[0].new_tables)
        self.assertEqual(2, len(changes[0].row_changes))

    def test_revision_rows_pair_by_unique_revision_when_newest_record_is_prepended(self) -> None:
        """Date reformatting must not shift every existing revision onto the next row."""

        old_table = TableVisual(
            page_number=3,
            table_number=1,
            title="Revision History",
            bbox=(10.0, 20.0, 500.0, 180.0),
            image_data_uri="",
            row_texts=[
                "表格行: T1 | Revision=2.0 | Date=2 Feb 2026 | Description=Second publication",
                "表格行: T1 | Revision=1.0 | Date=1 Jan 2026 | Description=First publication",
            ],
            grid_summary="structured rows",
            row_alignment_reliable=True,
        )
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "row_texts": [
                    "表格行: T1 | Revision=3.0 | Date=3 Mar 2026 | Description=Newest publication",
                    "表格行: T1 | Revision=2.0 | Date=2026-02-02 | Description=Second publication",
                    "表格行: T1 | Revision=1.0 | Date=2026-01-01 | Description=First publication",
                ],
            }
        )
        result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_table_visuals=[old_table],
            new_table_visuals=[new_table],
        )

        changes = reporting._build_table_changes(result)

        self.assertEqual(1, len(changes))
        self.assertEqual(3, len(changes[0].row_changes))
        by_item = {row.item: row for row in changes[0].row_changes}
        self.assertEqual(
            {"First publication", "Second publication", "Newest publication"},
            set(by_item),
        )
        self.assertEqual("新表新增行", by_item["Newest publication"].change_type)
        self.assertIn("Revision=3.0", by_item["Newest publication"].new_value)
        for description, revision in (
            ("First publication", "1.0"),
            ("Second publication", "2.0"),
        ):
            with self.subTest(description=description):
                row = by_item[description]
                self.assertEqual("替换/修改", row.change_type)
                self.assertIn(f"Revision={revision}", row.old_value)
                self.assertIn(f"Revision={revision}", row.new_value)

    def test_revision_schema_identity_keeps_same_page_records_in_physical_order(self) -> None:
        """Two schema-identical revision records cannot cross-pair on one page."""

        def table(number: int, top: float, rows: list[str]) -> TableVisual:
            return TableVisual(
                page_number=3,
                table_number=number,
                title="Revision History",
                bbox=(10.0, top, 500.0, top + 100.0),
                image_data_uri="",
                row_texts=rows,
                grid_summary="structured rows",
                row_alignment_reliable=True,
            )

        first_row = (
            "表格行: T1 | Revision=1.0 | Date=1 Jan 2026 | Description=Baseline A"
        )
        second_row = (
            "表格行: T2 | Revision=2.0 | Date=1 Jan 2026 | Description=Baseline B"
        )
        old_tables = [
            table(1, 20.0, [first_row]),
            table(2, 220.0, [second_row]),
        ]
        new_tables = [
            table(
                1,
                20.0,
                [
                    first_row,
                    "表格行: T1 | Revision=1.1 | Date=2 Feb 2026 | Description=Update A",
                ],
            ),
            table(2, 220.0, [second_row]),
        ]
        result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_table_visuals=old_tables,
            new_table_visuals=new_tables,
        )

        changes = reporting._build_table_changes(result)

        self.assertEqual(1, len(changes))
        self.assertEqual((old_tables[0],), changes[0].old_tables)
        self.assertEqual((new_tables[0],), changes[0].new_tables)
        self.assertEqual("新表新增行", changes[0].row_changes[0].change_type)

    def test_duplicate_revision_tables_front_insertion_preserves_unchanged_runs(self) -> None:
        """Exact A/B runs anchor monotonically when X is inserted before both."""

        def table(number: int, top: float, revision: str) -> TableVisual:
            return TableVisual(
                page_number=3,
                table_number=number,
                title="Revision History",
                bbox=(10.0, top, 500.0, top + 90.0),
                image_data_uri="",
                row_texts=[
                    "表格行: T1 | "
                    f"Revision={revision} | Date=1 Jan 2026 | Description=Release {revision}"
                ],
                grid_summary="structured rows",
                row_alignment_reliable=True,
            )

        old_tables = [table(1, 20.0, "A"), table(2, 140.0, "B")]
        new_tables = [
            table(1, 20.0, "X"),
            table(2, 140.0, "A"),
            table(3, 260.0, "B"),
        ]
        result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_table_visuals=old_tables,
            new_table_visuals=new_tables,
        )

        groups = reporting._paired_table_visuals(old_tables, new_tables)
        changes = reporting._build_table_changes(result)

        paired_revisions = [
            (
                reporting._table_row_fields(group.old_tables[0].row_texts[0])["revision"],
                reporting._table_row_fields(group.new_tables[0].row_texts[0])["revision"],
            )
            for group in groups
            if group.old_tables and group.new_tables
        ]
        self.assertEqual([("A", "A"), ("B", "B")], paired_revisions)
        self.assertEqual(1, len(changes))
        self.assertEqual("added", changes[0].change_type)
        self.assertIn("Revision=X", changes[0].new_tables[0].row_texts[0])

    def test_duplicate_revision_front_insertion_keeps_modified_run_paired(self) -> None:
        """A front insertion cannot turn a stable Revision edit into delete plus add."""

        def table(
            number: int,
            top: float,
            revision: str,
            description: str,
        ) -> TableVisual:
            return TableVisual(
                page_number=3,
                table_number=number,
                title="Revision History",
                bbox=(10.0, top, 500.0, top + 90.0),
                image_data_uri="",
                row_texts=[
                    "表格行: T1 | "
                    f"Revision={revision} | Date=1 Jan 2026 | Description={description}"
                ],
                grid_summary="structured rows",
                row_alignment_reliable=True,
            )

        old_tables = [
            table(1, 20.0, "A", "Release A"),
            table(2, 140.0, "B", "Release B"),
        ]
        new_tables = [
            table(1, 20.0, "X", "Release X"),
            table(2, 140.0, "A", "Release A"),
            table(3, 260.0, "B", "Release B revised"),
        ]
        result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_table_visuals=old_tables,
            new_table_visuals=new_tables,
        )

        groups = reporting._paired_table_visuals(old_tables, new_tables)
        changes = reporting._build_table_changes(result)
        paired_revisions = [
            (
                reporting._table_row_fields(group.old_tables[0].row_texts[0])["revision"],
                reporting._table_row_fields(group.new_tables[0].row_texts[0])["revision"],
            )
            for group in groups
            if group.old_tables and group.new_tables
        ]

        self.assertEqual([("A", "A"), ("B", "B")], paired_revisions)
        self.assertCountEqual(
            ["added", "modified"],
            [change.change_type for change in changes],
        )
        added = next(change for change in changes if change.change_type == "added")
        modified = next(change for change in changes if change.change_type == "modified")
        self.assertIn("Revision=X", added.new_tables[0].row_texts[0])
        self.assertEqual((old_tables[1],), modified.old_tables)
        self.assertEqual((new_tables[2],), modified.new_tables)
        self.assertEqual(1, len(modified.row_changes))
        self.assertEqual("替换/修改", modified.row_changes[0].change_type)
        self.assertIn("Release B revised", modified.row_changes[0].new_value)

    def test_revision_run_identity_rejects_missing_and_duplicate_values(self) -> None:
        """The duplicate-run pairing gate cannot guess a missing or repeated Revision."""

        missing = TableVisual(
            3,
            1,
            "Revision History",
            (10.0, 20.0, 500.0, 110.0),
            "",
            ["表格行: T1 | Date=1 Jan 2026 | Description=Missing identity"],
            "structured rows",
        )
        duplicate = TableVisual(
            3,
            2,
            "Revision History",
            (10.0, 140.0, 500.0, 260.0),
            "",
            [
                "表格行: T2 | Revision=B | Date=1 Jan 2026 | Description=First B",
                "表格行: T2 | Revision=B | Date=2 Jan 2026 | Description=Second B",
            ],
            "structured rows",
        )

        self.assertEqual((), reporting._revision_table_run_identity([missing], [0]))
        self.assertEqual((), reporting._revision_table_run_identity([duplicate], [0]))

    def test_revision_record_pairs_across_section_drift_with_shared_baseline(self) -> None:
        """A shared complete revision row outweighs added rows when section context drifts."""

        baseline = (
            "表格行: T1 | Revision=1.0 | Date=1 Jan 2026 | Description=Baseline"
        )
        old_table = TableVisual(
            3,
            1,
            "Revision History",
            (10.0, 20.0, 500.0, 160.0),
            "",
            [baseline],
            "structured rows",
            row_alignment_reliable=True,
        )
        new_table = TableVisual(
            4,
            1,
            "Revision History",
            (10.0, 20.0, 500.0, 200.0),
            "",
            [
                baseline,
                "表格行: T1 | Revision=1.1 | Date=2 Feb 2026 | Description=Editorial",
                "表格行: T1 | Revision=1.2 | Date=3 Mar 2026 | Description=Technical",
            ],
            "structured rows",
            row_alignment_reliable=True,
        )
        old_section = Section(
            "old-history",
            "1 Publication record",
            "Publication record",
            1,
            ("1 Publication record",),
            ("1",),
            3,
            3,
            "",
            role="document_metadata",
        )
        new_section = Section(
            "new-history",
            "2 Revision record",
            "Revision record",
            1,
            ("2 Revision record",),
            ("2",),
            4,
            4,
            "",
            role="document_metadata",
        )
        result = DiffResult(
            Path("old.pdf"),
            Path("new.pdf"),
            [old_section],
            [new_section],
            [],
            [],
            old_table_visuals=[old_table],
            new_table_visuals=[new_table],
        )

        changes = reporting._build_table_changes(result)

        self.assertEqual(1, len(changes))
        self.assertEqual("modified", changes[0].change_type)
        self.assertEqual("document_metadata", changes[0].role)
        self.assertEqual(2, len(changes[0].row_changes))

    def test_table_number_dash_glyph_and_spacing_do_not_create_caption_changes(self) -> None:
        """Supported single separators normalize only inside a strict table-number prefix."""

        row = "表格行: T1 | Parameter=Limit | Symbol=X | Value=10 | Units=dB"
        for dash in ("‐", "‑", "‒", "–", "—", "−"):
            for decorated in (f"32{dash}9", f"32 {dash} 9"):
                with self.subTest(decorated=decorated):
                    old_table = self._table()
                    old_table = TableVisual(
                        **{
                            **old_table.__dict__,
                            "title": "Table 32-9. Receiver limits",
                            "row_texts": [row],
                        }
                    )
                    new_table = TableVisual(
                        **{
                            **old_table.__dict__,
                            "title": f"Table {decorated}. Receiver limits",
                        }
                    )
                    result = DiffResult(
                        old_pdf=Path("old.pdf"),
                        new_pdf=Path("new.pdf"),
                        old_sections=[],
                        new_sections=[],
                        changes=[],
                        warnings=[],
                        old_table_visuals=[old_table],
                        new_table_visuals=[new_table],
                    )
                    self.assertEqual([], reporting._build_table_changes(result))

        invalid_new = TableVisual(
            **{
                **self._table().__dict__,
                "title": "Table 32--9. Receiver limits",
                "row_texts": [row],
            }
        )
        valid_old = TableVisual(
            **{
                **invalid_new.__dict__,
                "title": "Table 32-9. Receiver limits",
            }
        )
        invalid_result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_table_visuals=[valid_old],
            new_table_visuals=[invalid_new],
        )
        invalid_changes = reporting._build_table_changes(invalid_result)
        self.assertTrue(invalid_changes)
        self.assertTrue(
            any(change.caption_changed for change in invalid_changes)
        )  # 非法双横线可保守 fuzzy 配对或拆成增删，但绝不能被规范化成“无变化”。

    def test_pua_only_caption_delta_is_review_not_confirmed_change(self) -> None:
        """A lookalike caption code point stays visible without inflating material totals."""

        old_table = TableVisual(
            **{
                **self._table().__dict__,
                "title": "Table 1. \uf067 limits",
            }
        )
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "title": "Table 1. γ limits",
            }
        )
        result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_table_visuals=[old_table],
            new_table_visuals=[new_table],
        )

        changes = reporting._build_table_changes(result)

        self.assertEqual(1, len(changes))
        self.assertEqual("review", changes[0].change_type)
        self.assertFalse(changes[0].caption_changed)
        self.assertEqual(0, len(reporting._material_table_row_changes(changes[0])))
        self.assertEqual(1, len(reporting._table_review_rows(changes[0])))
        html = reporting._render_table_row_summary(changes[0])
        self.assertIn("表题字体编码复核", html)
        self.assertIn("U+F067", html)
        self.assertIn("U+03B3", html)

        materially_changed = TableVisual(
            **{
                **new_table.__dict__,
                "title": "Table 1. γ revised limits",
            }
        )
        material_result = DiffResult(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_table_visuals=[old_table],
            new_table_visuals=[materially_changed],
        )
        material_changes = reporting._build_table_changes(material_result)
        self.assertEqual(1, len(material_changes))
        self.assertEqual("modified", material_changes[0].change_type)
        self.assertTrue(material_changes[0].caption_changed)

    def test_single_unnumbered_table_similarity_uses_visible_title(self) -> None:
        """Empty strict caption sets cannot make changed unnumbered titles score 1.0."""

        old_table = TableVisual(
            **{
                **self._table().__dict__,
                "title": "Receiver limits",
            }
        )
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "title": "Transmitter limits",
            }
        )

        score = reporting._table_visual_group_similarity(
            (old_table,),
            (new_table,),
        )

        self.assertEqual(reporting._table_visual_similarity(old_table, new_table), score)
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)


if __name__ == "__main__":
    unittest.main()
