"""Document-independent table ordering invariants for rendered diff facts."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText, TableVisual
from protocol_pdf_diff.reporting import write_reports


class TableOrderGeneralityTests(unittest.TestCase):
    """Formatting order must not be mistaken for a table fact change."""

    def _table_changes(
        self,
        old_rows: list[str],
        new_rows: list[str],
        *,
        title: str = "Table 1 Operating limits",
        new_title: str | None = None,
    ) -> list[dict[str, object]]:
        body = (
            "Every recorded limit shall remain traceable to its condition and unit. "
            * 9
        )

        def extraction(name: str, rows: list[str], table_title: str) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[PageText(page_number=1, text=f"1 Requirements\n{body}")],
                total_pages=1,
                selected_start_page=1,
                selected_end_page=1,
                table_visuals=[
                    TableVisual(
                        page_number=1,
                        table_number=1,
                        title=table_title,
                        bbox=(0.0, 0.0, 100.0, 100.0),
                        image_data_uri="",
                        row_texts=rows,
                        grid_summary="structured rows",
                        content_fully_represented=True,
                        row_alignment_reliable=True,
                        data_rows_fully_represented=True,
                    )
                ],
            )

        options = DiffOptions()
        result = compare_extractions(
            extraction("old.pdf", old_rows, title),
            extraction("new.pdf", new_rows, new_title or title),
            options,
        )
        self.assertEqual("reliable", result.assessment.state)
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, options)
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
        return payload["table_changes"]

    def test_reordered_structured_fields_do_not_create_table_changes(self) -> None:
        old_rows = [
            "表格行: T1 | Parameter=Voltage | Symbol=VDD | Min=0.9 | Max=1.1 | Units=V"
        ]
        new_rows = [
            "表格行: T1 | Units=V | Max=1.1 | Min=0.9 | Symbol=VDD | Parameter=Voltage"
        ]

        self.assertEqual([], self._table_changes(old_rows, new_rows))

    def test_reordered_data_rows_do_not_become_added_or_deleted_facts(self) -> None:
        voltage = (
            "表格行: T1 | Parameter=Voltage | Symbol=VDD | "
            "Min=0.9 | Max=1.1 | Units=V"
        )
        current = (
            "表格行: T1 | Parameter=Current | Symbol=IDD | Max=10 | Units=mA"
        )

        self.assertEqual(
            [],
            self._table_changes([voltage, current], [current, voltage]),
        )

    def test_real_value_change_survives_field_and_row_reordering(self) -> None:
        old_rows = [
            (
                "表格行: T1 | Parameter=Voltage | Symbol=VDD | "
                "Min=0.9 | Max=1.1 | Units=V"
            ),
            "表格行: T1 | Parameter=Current | Symbol=IDD | Max=10 | Units=mA",
        ]
        new_rows = [
            "表格行: T1 | Units=mA | Max=10 | Symbol=IDD | Parameter=Current",
            (
                "表格行: T1 | Units=V | Max=1.2 | Min=0.9 | "
                "Symbol=VDD | Parameter=Voltage"
            ),
        ]

        table_changes = self._table_changes(
            old_rows,
            new_rows,
            title="Operating limits",
        )

        self.assertEqual(1, len(table_changes))
        self.assertEqual(1, table_changes[0]["row_change_count"])
        row_change = table_changes[0]["row_changes"][0]
        self.assertEqual("Voltage", row_change["item"])
        self.assertEqual("实质变化", row_change["change_type"])
        self.assertIn("1.1", row_change["old_value"])
        self.assertIn("1.2", row_change["new_value"])

    def test_technical_identifier_case_change_remains_visible_in_table(self) -> None:
        """Case is semantic for identifier-shaped values such as MODE_FAST."""

        old_rows = [
            "表格行: T1 | Parameter=Operating mode | Symbol=MODE_FAST | Value=Enabled"
        ]
        new_rows = [
            "表格行: T1 | Parameter=Operating mode | Symbol=mode_fast | Value=Enabled"
        ]

        table_changes = self._table_changes(old_rows, new_rows)

        self.assertEqual(1, len(table_changes))
        row_change = table_changes[0]["row_changes"][0]
        self.assertEqual("实质/符号变化", row_change["change_type"])
        self.assertIn("MODE_FAST", row_change["old_value"])
        self.assertIn("mode_fast", row_change["new_value"])

    def test_ordinary_initial_capitalization_is_not_a_table_change(self) -> None:
        """Sentence-style capitalization alone stays formatting noise."""

        old_rows = [
            "表格行: T1 | Parameter=Operating mode | Value=Enabled by default"
        ]
        new_rows = [
            "表格行: T1 | Parameter=Operating mode | Value=enabled by default"
        ]

        self.assertEqual([], self._table_changes(old_rows, new_rows))

    def test_technical_identifier_case_change_remains_visible_in_caption(self) -> None:
        """Table captions obey the same identifier-case rule as body and cells."""

        row = "表格行: T1 | Parameter=Voltage | Symbol=VDD | Max=1.1 | Units=V"
        table_changes = self._table_changes(
            [row],
            [row],
            title="Table 1 MODE_FAST limits",
            new_title="Table 1 mode_fast limits",
        )

        self.assertEqual(1, len(table_changes))
        self.assertTrue(table_changes[0]["caption_changed"])

    def test_ordinary_caption_initial_capitalization_is_ignored(self) -> None:
        """A sentence-style initial capital is display formatting, not a fact."""

        row = "表格行: T1 | Parameter=Voltage | Symbol=VDD | Max=1.1 | Units=V"
        self.assertEqual(
            [],
            self._table_changes(
                [row],
                [row],
                title="Operating limits",
                new_title="operating limits",
            ),
        )

    def test_case_sensitive_unit_in_field_label_remains_visible(self) -> None:
        """Unknown field labels may carry units whose case changes their meaning."""

        old_rows = ["表格行: T1 | Parameter=Power | Power (mW)=10"]
        new_rows = ["表格行: T1 | Parameter=Power | Power (MW)=10"]

        table_changes = self._table_changes(old_rows, new_rows)

        self.assertEqual(1, len(table_changes))
        row_change = table_changes[0]["row_changes"][0]
        self.assertIn("Power (mW)=10", row_change["old_value"])
        self.assertIn("Power (MW)=10", row_change["new_value"])

    def test_math_operator_change_remains_visible_in_table_value(self) -> None:
        """Table normalization must not erase a plus/minus formula edit."""

        old_rows = ["表格行: T1 | Parameter=Combination | Value=A + B"]
        new_rows = ["表格行: T1 | Parameter=Combination | Value=A - B"]

        table_changes = self._table_changes(old_rows, new_rows)

        self.assertEqual(1, len(table_changes))
        row_change = table_changes[0]["row_changes"][0]
        self.assertIn("A + B", row_change["old_value"])
        self.assertIn("A - B", row_change["new_value"])

    def test_unnumbered_table_still_pairs_after_field_and_row_reordering(self) -> None:
        old_rows = [
            (
                "表格行: T1 | Parameter=Voltage | Symbol=VDD | "
                "Min=0.9 | Max=1.1 | Units=V"
            ),
            "表格行: T1 | Parameter=Current | Symbol=IDD | Max=10 | Units=mA",
        ]
        new_rows = [
            "表格行: T1 | Units=mA | Max=10 | Symbol=IDD | Parameter=Current",
            (
                "表格行: T1 | Units=V | Max=1.1 | Min=0.9 | "
                "Symbol=VDD | Parameter=Voltage"
            ),
        ]

        self.assertEqual(
            [],
            self._table_changes(old_rows, new_rows, title="Operating limits"),
        )


if __name__ == "__main__":
    unittest.main()
