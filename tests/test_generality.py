"""Document-agnostic regression tests for the PDF comparison core."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from protocol_pdf_diff.compare import (
    _split_units,
    _table_row_cells,
    _table_row_fields,
    compare_extractions,
    run_diff,
)
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText, TableVisual
from protocol_pdf_diff.pdf_extract import (
    _combine_text_and_table_lines,
    _drop_value_alignment_noise,
    _format_table_row,
    _table_lines_from_rows,
)
from protocol_pdf_diff.sample_data import write_multipage_text_pdf
from protocol_pdf_diff.sectioning import detect_heading, section_document


class DocumentGeneralityTests(unittest.TestCase):
    """Guard behavior that must not depend on one vendor or specification."""

    def test_structured_table_codec_preserves_cell_and_field_boundaries(self) -> None:
        """Pipe and equals characters inside cells cannot collide with row syntax."""

        for old_row, new_row in (
            (
                _format_table_row(["ready|enabled"], ["Value"], 1),
                _format_table_row(["ready", "enabled"], ["Value", "Column 2"], 1),
            ),
            (
                _format_table_row(["c"], ["a=b"], 1),
                _format_table_row(["b=c"], ["a"], 1),
            ),
        ):
            with self.subTest(old_row=old_row, new_row=new_row):
                self.assertNotEqual(old_row, new_row)
                old_structure = (_table_row_cells(old_row), _table_row_fields(old_row))
                new_structure = (_table_row_cells(new_row), _table_row_fields(new_row))
                self.assertNotEqual(old_structure, new_structure)

    def test_empty_table_columns_remain_structurally_observable(self) -> None:
        """An empty reserved column is still part of the document's table schema."""

        old_lines = _table_lines_from_rows(
            [["Parameter", "Value"], ["p", "1"]],
            1,
        )
        new_lines = _table_lines_from_rows(
            [["Parameter", "Value", "Notes"], ["p", "1", ""]],
            1,
        )
        self.assertNotEqual(old_lines, new_lines)
        self.assertIn("Notes=", new_lines[0])

        old_headerless = _table_lines_from_rows([["A", "B"]], 2)
        new_headerless = _table_lines_from_rows([["A", "B", ""]], 2)
        self.assertNotEqual(old_headerless, new_headerless)
        self.assertIn("Column 3=", new_headerless[0])

    def test_duplicate_header_empty_cells_preserve_physical_column_position(self) -> None:
        """Moving a value between duplicate columns cannot collapse to the same row."""

        header = ["Parameter", "Value", "Value", "Value"]
        old_lines = _table_lines_from_rows([header, ["p", "1", "", "2"]], 1)
        new_lines = _table_lines_from_rows([header, ["p", "1", "2", ""]], 1)

        self.assertNotEqual(old_lines, new_lines)
        self.assertEqual(3, old_lines[0].count("Value="))
        self.assertEqual(3, new_lines[0].count("Value="))

    def test_table_rows_are_not_merged_from_text_shape_alone(self) -> None:
        """A second physical row beginning with of/to/and can be real data."""

        header = ["Parameter", "Symbol", "Value"]
        for old_rows, new_rows in (
            (
                [header, ["Probability", "P", "0.5"], ["of failure", "F", ""]],
                [header, ["Probability of failure", "PF", "0.5"]],
            ),
            (
                [header, ["Ratio", "R", "1"], ["to ground", "G", ""]],
                [header, ["Ratio to ground", "RG", "1"]],
            ),
        ):
            with self.subTest(old_rows=old_rows, new_rows=new_rows):
                old_lines = _table_lines_from_rows(old_rows, 1)
                new_lines = _table_lines_from_rows(new_rows, 1)
                self.assertEqual(2, len(old_lines))
                self.assertEqual(1, len(new_lines))
                self.assertNotEqual(old_lines, new_lines)

    def test_table_cell_line_break_does_not_collide_with_literal_slash(self) -> None:
        """A multi-line cell and a one-line division/alternative remain distinct."""

        multiline = _table_lines_from_rows(
            [["Parameter", "Value"], ["Expression", "ready\nenabled"]],
            1,
        )
        literal_slash = _table_lines_from_rows(
            [["Parameter", "Value"], ["Expression", "ready / enabled"]],
            1,
        )

        self.assertNotEqual(multiline, literal_slash)
        self.assertIn(r"ready\nenabled", multiline[0])
        self.assertIn("ready / enabled", literal_slash[0])

    def test_same_page_unnumbered_table_context_ambiguity_degrades_confidence(self) -> None:
        """Page-only context cannot assign multiple unnumbered tables to sections."""

        body = "Each section preserves independently reviewable requirements and limits. " * 12
        extraction = ExtractionResult(
            pdf_path=Path("same-page-tables.pdf"),
            pages=[PageText(page_number=1, text=f"1 Alpha\n{body}\n2 Beta\n{body}")],
            table_visuals=[
                TableVisual(
                    page_number=1,
                    table_number=index,
                    title="Limits",
                    bbox=(0.0, float(index * 100), 100.0, float(index * 100 + 80)),
                    image_data_uri="",
                    row_texts=[f"表格行: T{index} | Parameter=Limit | Value={index}"],
                    grid_summary="structured rows",
                )
                for index in (1, 2)
            ],
        )

        result = compare_extractions(extraction, extraction, DiffOptions())

        self.assertEqual("degraded", result.assessment.state)
        self.assertGreater(
            result.assessment.old_document.ambiguous_table_context_page_count,
            0,
        )

    def test_single_selected_page_metadata_cluster_is_preserved_without_repetition(self) -> None:
        """One selected page cannot prove that title, revision, and date are furniture."""

        extraction = ExtractionResult(
            pdf_path=Path("northstar-handbook.pdf"),
            pages=[
                PageText(
                    page_number=17,
                    text=(
                        "Northstar Interconnect Validation Handbook | 17\n"
                        "Revision 2.4, Version 1.1\n"
                        "March 8, 2027\n"
                        "4 Requirements\n"
                        "Every implementation shall preserve the declared operating limits."
                    ),
                )
            ],
            total_pages=80,
            selected_start_page=17,
            selected_end_page=17,
        )

        sections = section_document(extraction)
        visible_text = "\n".join(section.comparable_text for section in sections)

        self.assertIn("Northstar Interconnect Validation Handbook", visible_text)
        self.assertIn("Revision 2.4", visible_text)
        self.assertIn("March 8, 2027", visible_text)
        self.assertIn("Every implementation shall preserve", visible_text)

    def test_short_pipe_delimited_content_is_not_guessed_to_be_a_page_header(self) -> None:
        """A short field/value row needs more evidence before destructive filtering."""

        extraction = ExtractionResult(
            pdf_path=Path("status-register.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Results\n"
                        "Status | 1\n"
                        "The register shall preserve every recorded state and transition."
                    ),
                )
            ],
        )

        visible_text = "\n".join(
            section.comparable_text for section in section_document(extraction)
        )

        self.assertIn("Status | 1", visible_text)

    def test_page_bearing_line_without_cluster_is_preserved(self) -> None:
        """Length and a trailing number alone do not prove that a margin line is furniture."""

        extraction = ExtractionResult(
            pdf_path=Path("operational-register.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "Operational Register State | 17\n"
                        "1 Results\n"
                        "The register shall preserve every recorded state and transition."
                    ),
                )
            ],
        )

        visible_text = "\n".join(
            section.comparable_text for section in section_document(extraction)
        )

        self.assertIn("Operational Register State | 17", visible_text)

    def test_single_page_header_shaped_revision_change_is_preserved(self) -> None:
        """Text shape alone cannot prove that a one-page title/version cluster is furniture."""

        shared = (
            "Every implementation shall preserve all declared operating limits and "
            "traceable review evidence. "
        ) * 8
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-window.pdf"),
                pages=[
                    PageText(
                        page_number=3,
                        text=f"Maintenance Window | 3\nVersion 1.0\n1 Scope\n{shared}",
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-window.pdf"),
                pages=[
                    PageText(
                        page_number=3,
                        text=f"Maintenance Window | 3\nVersion 2.0\n1 Scope\n{shared}",
                    )
                ],
            ),
            DiffOptions(),
        )

        self.assertTrue(result.changes)
        changed_text = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )
        self.assertIn("Version 1.0", changed_text)
        self.assertIn("Version 2.0", changed_text)

    def test_repeated_edge_requirement_change_is_not_learned_as_furniture(self) -> None:
        """A requirement repeated on each page remains content on both sides."""

        paragraph = (
            "Every implementation shall preserve all declared limits and traceable evidence. "
        ) * 5

        def extraction(name: str, state: str) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=page,
                        text=(
                            f"Operating mode shall be {state}.\n"
                            f"{page} Requirements {page}\n{paragraph}"
                        ),
                    )
                    for page in range(1, 4)
                ],
                total_pages=3,
                selected_start_page=1,
                selected_end_page=3,
            )

        result = compare_extractions(
            extraction("old-edge.pdf", "Disabled"),
            extraction("new-edge.pdf", "Enabled"),
            DiffOptions(),
        )

        self.assertTrue(result.changes)

    def test_page_number_prefix_does_not_hide_repeated_requirement_values(self) -> None:
        """Mentioning Page N inside a sentence is not a page-counter-only shape."""

        paragraph = (
            "Every implementation shall preserve all declared limits and traceable evidence. "
        ) * 5

        def extraction(name: str, timeout: int) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=page,
                        text=(
                            f"Page {page} timeout shall be {timeout} ms.\n"
                            f"{page} Requirements {page}\n{paragraph}"
                        ),
                    )
                    for page in range(1, 4)
                ],
                total_pages=3,
                selected_start_page=1,
                selected_end_page=3,
            )

        result = compare_extractions(
            extraction("old-timeout.pdf", 10),
            extraction("new-timeout.pdf", 20),
            DiffOptions(),
        )

        self.assertTrue(result.changes)

    def test_repeated_pure_numeric_edge_values_are_preserved(self) -> None:
        """A numeric sequence may be a vector or channel set, not a page counter."""

        paragraph = (
            "Every implementation shall preserve all declared limits and traceable evidence. "
        ) * 5

        def extraction(name: str, offset: int) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=page,
                        text=f"{offset + page}\n{page} Requirements {page}\n{paragraph}",
                    )
                    for page in range(1, 4)
                ],
                total_pages=3,
                selected_start_page=1,
                selected_end_page=3,
            )

        result = compare_extractions(
            extraction("old-numeric-edge.pdf", 9),
            extraction("new-numeric-edge.pdf", 19),
            DiffOptions(),
        )

        self.assertTrue(result.changes)

    def test_headerless_table_uses_neutral_columns_and_preserves_opaque_values(self) -> None:
        """Column count alone must not invent electrical-table semantics."""

        lines = _table_lines_from_rows(
            [["Widget A", "Owner Q", "T", "Pending"]],
            table_number=1,
        )

        self.assertEqual(
            [
                "表格行: T1 | Column 1=Widget A | Column 2=Owner Q | "
                "Column 3=T | Column 4=Pending"
            ],
            lines,
        )
        self.assertFalse(any("Parameter=" in line or "Symbol=" in line for line in lines))

        for width in range(4, 8):
            with self.subTest(width=width):
                cells = [f"Field {index}" for index in range(1, width + 1)]
                neutral_lines = _table_lines_from_rows([cells], table_number=2)
                self.assertEqual(1, len(neutral_lines))
                for index, value in enumerate(cells, start=1):
                    self.assertIn(f"Column {index}={value}", neutral_lines[0])
                self.assertNotIn("Parameter=", neutral_lines[0])
                self.assertNotIn("Symbol=", neutral_lines[0])

    def test_observed_table_value_is_never_deleted_as_sample_specific_noise(self) -> None:
        """An isolated T in a real Value column remains observable evidence."""

        lines = _table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value", "Units"],
                [
                    "Length\nCapacitance\nImpedance\nImpedance 2",
                    "L\nC\nZ\nZ2",
                    "44\n40\n87.5\nT\n95",
                    "mm\nfF\nohm\nohm",
                ],
            ],
            table_number=1,
        )

        self.assertTrue(any("T" in line for line in lines), lines)
        self.assertEqual(
            ["44", "40", "87.5", "T", "95"],
            _drop_value_alignment_noise(["44", "40", "87.5", "T", "95"], 4),
        )

    def test_conflicting_headerless_multiline_counts_stay_aggregate(self) -> None:
        """A colon or familiar noun cannot prove that the first observed cell line is disposable."""

        lines = _table_lines_from_rows(
            [["Model: Alpha\nLimit A\nLimit B", "X\nY", "1\n2", "V\nV"]],
            table_number=1,
        )

        self.assertEqual(1, len(lines))
        self.assertIn(r"Column 1=Model: Alpha\nLimit A\nLimit B", lines[0])
        self.assertIn(r"Column 2=X\nY", lines[0])
        self.assertIn(r"Column 3=1\n2", lines[0])
        self.assertIn(r"Column 4=V\nV", lines[0])

    def test_adjacent_complete_symbol_values_are_not_concatenated(self) -> None:
        """Two uppercase symbol rows are stronger evidence than a guessed base/subscript pair."""

        lines = _table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value"],
                ["Length\nCapacitance", "L\nC", "1\n2"],
            ],
            table_number=1,
        )

        self.assertEqual(2, len(lines))
        self.assertIn("Parameter=Length | Symbol=L | Value=1", lines[0])
        self.assertIn("Parameter=Capacitance | Symbol=C | Value=2", lines[1])

    def test_raw_table_text_is_preserved_alongside_structured_evidence(self) -> None:
        """Token overlap cannot prove raw and structured observations are identical."""

        structured = [
            "表格行: T1 | Item=A1 | State=Open | Code=17",
            "表格行: T1 | Item=B2 | State=Closed | Code=23",
        ]
        raw = "A1 Open 17 B2 Closed 23"

        combined = _combine_text_and_table_lines(raw, structured)
        units = _split_units("\n".join([raw, *structured]))

        self.assertIn(raw, combined)
        self.assertIn(raw, units)
        self.assertTrue(all(line in combined for line in structured))

    def test_raw_table_operator_order_and_multiplicity_are_never_suppressed(self) -> None:
        """Sets of cell words cannot represent operators, order, or repeated values."""

        structured = ["表格行: T1 | Item=A1 | State=Open | Code=17"]
        observations = [
            "A1 != Open 17",
            "A1 = Open 17",
            "Open A1 17",
            "A1 Open Open 17",
        ]

        combined = _combine_text_and_table_lines("\n".join(observations), structured)
        units = _split_units("\n".join([*observations, *structured]))

        for observation in observations:
            self.assertIn(observation, combined)
        self.assertIn("A1 !", units)
        self.assertIn("= Open 17", units)
        self.assertIn("A1 = Open 17", units)
        self.assertIn("Open A1 17", units)
        self.assertIn("A1 Open Open 17", units)

    def test_sentence_overlapping_one_table_row_is_not_removed(self) -> None:
        """Token overlap alone cannot erase a normative sentence."""

        structured = ["表格行: T1 | Item=A1 | State=Open | Code=17"]
        sentence = "The A1 state is Open and code 17 shall remain documented."

        combined = _combine_text_and_table_lines(sentence, structured)
        units = _split_units("\n".join([sentence, *structured]))

        self.assertIn(sentence, combined)
        self.assertIn(sentence, units)

    def test_action_sentence_overlapping_table_values_is_not_removed(self) -> None:
        """Unknown prose verbs cannot be used as a deletion whitelist."""

        structured = ["表格行: T1 | Item=A1 | State=Open | Code=17 | Mode=Safe"]
        old_sentence = "Configure item A1 with state Open code 17 mode Safe."
        new_sentence = "Disable item A1 with state Open code 17 mode Safe."
        shared = (
            "Every recorded configuration shall remain traceable throughout validation. "
        ) * 9
        old_text = _combine_text_and_table_lines(old_sentence, structured)
        new_text = _combine_text_and_table_lines(new_sentence, structured)
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-actions.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{shared}\n{old_text}")],
            ),
            ExtractionResult(
                pdf_path=Path("new-actions.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{shared}\n{new_text}")],
            ),
            DiffOptions(),
        )

        self.assertIn(old_sentence, old_text)
        self.assertIn(new_sentence, new_text)
        self.assertTrue(result.changes)

    def test_one_uncovered_word_prevents_raw_table_suppression(self) -> None:
        """High token overlap never authorizes dropping an uncovered semantic word."""

        structured = [
            "表格行: T1 | Item=A1 | State=Open | Code=17 | Mode=Safe | Speed=Fast | Region=North"
        ]
        old_sentence = "Allow A1 Open 17 Safe Fast North"
        new_sentence = "Block A1 Open 17 Safe Fast North"

        old_text = _combine_text_and_table_lines(old_sentence, structured)
        new_text = _combine_text_and_table_lines(new_sentence, structured)

        self.assertIn(old_sentence, old_text)
        self.assertIn(new_sentence, new_text)
        self.assertIn(old_sentence, _split_units(old_text))
        self.assertIn(new_sentence, _split_units(new_text))

    def test_unknown_multiline_column_does_not_infer_symbol_subscripts(self) -> None:
        """Neutral columns preserve observed line boundaries instead of guessing symbols."""

        old_lines = _table_lines_from_rows(
            [["Item A\nItem B", "R\n1", "Open\nClosed", "x\ny"]],
            table_number=1,
        )
        new_lines = _table_lines_from_rows(
            [["Item A\nItem B", "R1", "Open\nClosed", "x\ny"]],
            table_number=1,
        )

        self.assertNotEqual(old_lines, new_lines)
        old_text = "\n".join(old_lines)
        new_text = "\n".join(new_lines)
        self.assertIn(r"Column 2=R\n1", old_text)
        self.assertIn("Column 2=R1", new_text)
        self.assertEqual(1, len(old_lines))
        self.assertEqual(1, len(new_lines))

    def test_unknown_aggregate_column_preserves_every_line_boundary(self) -> None:
        """Ambiguous neutral columns never concatenate fragments into a guessed symbol."""

        old_lines = _table_lines_from_rows(
            [["Item A\nItem B\nItem C", "C\np\nZ\nc2", "Open\nClosed", "x\ny"]],
            table_number=1,
        )
        new_lines = _table_lines_from_rows(
            [["Item A\nItem B\nItem C", "CpZc2", "Open\nClosed", "x\ny"]],
            table_number=1,
        )

        self.assertNotEqual(old_lines, new_lines)
        self.assertIn(r"Column 2=C\np\nZ\nc2", old_lines[0])
        self.assertIn("Column 2=CpZc2", new_lines[0])

    def test_single_letter_mode_change_is_never_erased_as_watermark_noise(self) -> None:
        """Opaque R/T values in prose are user content without layout evidence."""

        shared = (
            "The controller shall preserve every declared timing, voltage, and "
            "interoperability requirement throughout calibration. "
        ) * 8
        old = ExtractionResult(
            pdf_path=Path("old-modes.pdf"),
            pages=[PageText(page_number=1, text=f"1 Modes\n{shared}\nSelect mode R before calibration.")],
        )
        new = ExtractionResult(
            pdf_path=Path("new-modes.pdf"),
            pages=[PageText(page_number=1, text=f"1 Modes\n{shared}\nSelect mode T before calibration.")],
        )

        result = compare_extractions(old, new, DiffOptions())
        changed_text = "\n".join(
            [
                *(text for change in result.changes for text in change.removed_snippets),
                *(text for change in result.changes for text in change.added_snippets),
                *(pair.old for change in result.changes for pair in change.replaced_snippets),
                *(pair.new for change in result.changes for pair in change.replaced_snippets),
            ]
        )

        self.assertTrue(result.changes)
        self.assertIn("mode R", changed_text)
        self.assertIn("mode T", changed_text)

    def test_standard_name_and_version_change_is_preserved_in_body_prose(self) -> None:
        """A known publication name is content when it appears in a real sentence."""

        shared = (
            "The product shall retain all normative behavior and documented interfaces. "
        ) * 8
        old = ExtractionResult(
            pdf_path=Path("old-reference.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 References\n{shared}\n"
                        "The Implementation Agreement OIF-CEI 5.1 shall be used."
                    ),
                )
            ],
        )
        new = ExtractionResult(
            pdf_path=Path("new-reference.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 References\n{shared}\n"
                        "The Implementation Agreement OIF-CEI 5.2 shall be used."
                    ),
                )
            ],
        )

        result = compare_extractions(old, new, DiffOptions())
        changed_text = "\n".join(
            [
                *(text for change in result.changes for text in change.removed_snippets),
                *(text for change in result.changes for text in change.added_snippets),
                *(pair.old for change in result.changes for pair in change.replaced_snippets),
                *(pair.new for change in result.changes for pair in change.replaced_snippets),
            ]
        )

        self.assertTrue(result.changes)
        self.assertIn("OIF-CEI 5.1", changed_text)
        self.assertIn("OIF-CEI 5.2", changed_text)

    def test_standalone_formula_value_change_is_preserved_without_figure_evidence(self) -> None:
        """Formula-shaped selectable text is content unless layout marks it visual."""

        shared = (
            "The measurement procedure shall retain every declared condition and limit. "
        ) * 8
        old = ExtractionResult(
            pdf_path=Path("old-formula.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=f"1 Calculation\n{shared}\nSNDR = 20 - 10 log10(2)",
                )
            ],
        )
        new = ExtractionResult(
            pdf_path=Path("new-formula.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=f"1 Calculation\n{shared}\nSNDR = 20 - 10 log10(3)",
                )
            ],
        )

        result = compare_extractions(old, new, DiffOptions())
        changed_text = "\n".join(
            [
                *(pair.old for change in result.changes for pair in change.replaced_snippets),
                *(pair.new for change in result.changes for pair in change.replaced_snippets),
            ]
        )

        self.assertTrue(result.changes)
        self.assertIn("log10(2)", changed_text)
        self.assertIn("log10(3)", changed_text)

    def test_real_pdf_formula_change_survives_text_extraction(self) -> None:
        """The full PDF path must not discard equations based on text shape alone."""

        def write_pdf(path: Path, formula: str) -> None:
            write_multipage_text_pdf(
                path,
                [[
                    "1 Calculation",
                    *(
                        "The procedure shall preserve every timing voltage and calibration condition."
                        for _ in range(9)
                    ),
                    formula,
                ]],
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            old_pdf = Path(temp_dir) / "old.pdf"
            new_pdf = Path(temp_dir) / "new.pdf"
            write_pdf(old_pdf, "SNDR = 20 - 10 log10(2)")
            write_pdf(new_pdf, "SNDR = 20 - 10 log10(3)")

            result = run_diff(old_pdf, new_pdf, DiffOptions())

        changed_text = "\n".join(
            [
                *(pair.old for change in result.changes for pair in change.replaced_snippets),
                *(pair.new for change in result.changes for pair in change.replaced_snippets),
            ]
        )
        self.assertTrue(result.changes)
        self.assertIn("log10(2)", changed_text)
        self.assertIn("log10(3)", changed_text)

    def test_opaque_identifier_change_is_not_treated_as_table_noise(self) -> None:
        """Uppercase and underscore identifiers remain comparable user content."""

        shared = (
            "The configuration shall retain every declared interface and safety condition. "
        ) * 8
        old = ExtractionResult(
            pdf_path=Path("old-identifier.pdf"),
            pages=[PageText(page_number=1, text=f"1 Configuration\n{shared}\nMODE_FAST")],
        )
        new = ExtractionResult(
            pdf_path=Path("new-identifier.pdf"),
            pages=[PageText(page_number=1, text=f"1 Configuration\n{shared}\nMODE_SAFE")],
        )

        result = compare_extractions(old, new, DiffOptions())
        changed_text = "\n".join(
            [
                *(text for change in result.changes for text in change.removed_snippets),
                *(text for change in result.changes for text in change.added_snippets),
                *(pair.old for change in result.changes for pair in change.replaced_snippets),
                *(pair.new for change in result.changes for pair in change.replaced_snippets),
            ]
        )

        self.assertTrue(result.changes)
        self.assertIn("MODE_FAST", changed_text)
        self.assertIn("MODE_SAFE", changed_text)

    def test_multipart_contents_and_body_can_restart_local_numbering(self) -> None:
        """Part hierarchy, not a global number set, defines section identity."""

        extraction = ExtractionResult(
            pdf_path=Path("multipart-policy.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "Contents\n"
                        "Part I General\n"
                        "1 Scope ........ 3\n"
                        "2 Terms ........ 4\n"
                        "Part II Operations\n"
                        "1 Scope ........ 9\n"
                        "2 Controls ........ 10"
                    ),
                ),
                PageText(
                    page_number=3,
                    text=(
                        "Part I General\n"
                        "1 Scope\nThe general scope applies to every participating organization.\n"
                        "2 Terms\nThe general terms define all shared obligations."
                    ),
                ),
                PageText(
                    page_number=9,
                    text=(
                        "Part II Operations\n"
                        "1 Scope\nThe operational scope applies to deployed services.\n"
                        "2 Controls\nThe operational controls shall remain enforceable."
                    ),
                ),
            ],
            total_pages=12,
        )

        technical_sections = [
            section for section in section_document(extraction) if section.role == "technical"
        ]

        self.assertEqual(
            [
                ("Part I",),
                ("Part I", "1"),
                ("Part I", "2"),
                ("Part II",),
                ("Part II", "1"),
                ("Part II", "2"),
            ],
            [section.number_path for section in technical_sections],
        )
        visible_text = "\n".join(section.body for section in technical_sections)
        self.assertIn("general scope", visible_text)
        self.assertIn("operational scope", visible_text)

    def test_annex_subclauses_keep_their_local_hierarchy(self) -> None:
        """Annex A and A.1/A.2 are common document structure, not vendor syntax."""

        extraction = ExtractionResult(
            pdf_path=Path("generic-standard-with-annex.pdf"),
            pages=[
                PageText(
                    page_number=20,
                    text=(
                        "Annex A Test methods\n"
                        "A.1 Setup\nThe laboratory shall record every declared condition.\n"
                        "A.2 Procedure\nThe operator shall execute every normative step."
                    ),
                )
            ],
            total_pages=24,
            selected_start_page=20,
            selected_end_page=20,
        )

        technical_sections = [
            section for section in section_document(extraction) if section.role == "technical"
        ]

        self.assertEqual(
            [("Annex A",), ("Annex A", "A.1"), ("Annex A", "A.2")],
            [section.number_path for section in technical_sections],
        )
        self.assertEqual(
            ["Test methods", "Setup", "Procedure"],
            [section.title for section in technical_sections],
        )

    def test_numeric_letter_appendices_keep_distinct_hierarchy(self) -> None:
        """OIF-style 31.A/31.A.1 markers must not collapse to numeric parent 31."""

        extraction = ExtractionResult(  # 使用真实规范常见的数字主章加字母附录层级作为独立输入。
            pdf_path=Path("numeric-letter-appendices.pdf"),
            pages=[
                PageText(
                    page_number=29,
                    text=(
                        "31 Channel Requirements\n"
                        "The channel requirements apply to every implementation.\n"
                        "31.A Host Compliance Board\n"
                        "The host board shall preserve the declared impedance.\n"
                        "31.A.1 Insertion Loss\n"
                        "Insertion loss shall remain within the stated mask.\n"
                        "31.B Module Compliance Board\n"
                        "The module board shall preserve the declared impedance.\n"
                        "31.C Reference Receiver\n"
                        "The receiver model defines the compliance reference.\n"
                        "31.C.3 Receiver Noise\n"
                        "Receiver noise shall remain within the stated limit."
                    ),
                )
            ],
            total_pages=40,
            selected_start_page=29,
            selected_end_page=29,
        )

        technical_sections = [  # 只检查参与技术差异匹配的章节路径。
            section for section in section_document(extraction) if section.role == "technical"
        ]

        self.assertEqual(  # 每个字母后缀都必须保留为独立身份，并按点号深度嵌套。
            [
                ("31",),
                ("31", "31.A"),
                ("31", "31.A", "31.A.1"),
                ("31", "31.B"),
                ("31", "31.C"),
                ("31", "31.C", "31.C.3"),
            ],
            [section.number_path for section in technical_sections],
        )
        self.assertEqual(  # 身份键必须包含完整路径，避免 31.A、31.B、31.C 形成重复候选。
            len(technical_sections),
            len({section.identity_key for section in technical_sections}),
        )

    def test_short_numeric_letter_titles_keep_their_complete_identity(self) -> None:
        """Short appendix titles must not fall through to the pure-integer rule."""

        expected_numbers = {  # Host/Loss 是合法短标题；小写单字母 f 则仍是无标题符号碎片。
            "31.A Host": "31.A",
            "31.A.1 Loss": "31.A.1",
        }
        for source_line, expected_number in expected_numbers.items():  # 每个层级都独立守住完整混合编号。
            with self.subTest(source_line=source_line):
                heading = detect_heading(source_line)  # 走公开章节识别入口，不绑定内部正则顺序。

                self.assertIsNotNone(heading)  # 合法短标题必须形成章节。
                self.assertEqual(expected_number, heading.number)  # 编号不能退化成纯整数 31。

        self.assertIsNone(  # mixed 规则拒绝的符号碎片不得再由后续 numeric 规则降级接收。
            detect_heading("31.A f")
        )

    def test_ambiguous_number_restart_is_preserved_and_cannot_be_reliable(self) -> None:
        """Unknown subdocument boundaries keep both occurrences and lower confidence."""

        body_a = (
            "The first embedded policy shall preserve every declared responsibility. "
        ) * 5
        body_b = (
            "The second embedded policy shall preserve every declared responsibility. "
        ) * 5
        extraction = ExtractionResult(
            pdf_path=Path("combined-policies.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Scope\n{body_a}\n2 Controls\n{body_a}\n"
                        f"1 Scope\n{body_b}\n2 Controls\n{body_b}"
                    ),
                )
            ],
        )

        result = compare_extractions(extraction, extraction, DiffOptions())
        scopes = [
            section for section in result.old_sections if section.number_path == ("1",)
        ]
        controls = [
            section for section in result.old_sections if section.number_path == ("2",)
        ]

        self.assertEqual(2, len(scopes))
        self.assertEqual(2, len(controls))
        self.assertEqual("degraded", result.assessment.state)
        self.assertGreater(result.assessment.old_document.duplicate_number_path_count, 0)

    def test_duplicate_number_paths_match_by_content_when_subdocuments_reorder(self) -> None:
        """A reordered embedded document must not be paired only by first occurrence."""

        body_a = (
            "Policy Alpha shall preserve every declared voltage and timing obligation. "
        ) * 8
        body_b = (
            "Policy Beta shall preserve every declared ownership and archival obligation. "
        ) * 8

        def extraction(name: str, first: str, second: str) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            f"1 Scope\n{first}\n2 Controls\n{first}\n"
                            f"1 Scope\n{second}\n2 Controls\n{second}"
                        ),
                    )
                ],
            )

        result = compare_extractions(
            extraction("old-combined.pdf", body_a, body_b),
            extraction("new-combined.pdf", body_b, body_a),
            DiffOptions(),
        )

        self.assertEqual("degraded", result.assessment.state)
        self.assertEqual([], result.changes)

    def test_document_control_metadata_cannot_make_tiny_body_reliable(self) -> None:
        """Approval and distribution records do not count as technical coverage."""

        metadata = " ".join(
            f"Owner {index} approved release distribution and archival status."
            for index in range(1, 18)
        )
        extraction = ExtractionResult(
            pdf_path=Path("controlled-policy.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        f"1 Document Control\n{metadata}\n"
                        "2 Requirement\nThe device shall stop."
                    ),
                )
            ],
        )

        result = compare_extractions(extraction, extraction, DiffOptions())
        roles = {section.title: section.role for section in result.old_sections}

        self.assertEqual("document_metadata", roles["Document Control"])
        self.assertEqual("technical", roles["Requirement"])
        self.assertEqual("degraded", result.assessment.state)
        self.assertLess(result.assessment.old_document.technical_character_count, 500)


if __name__ == "__main__":
    unittest.main()
