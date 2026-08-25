"""Reader-facing regressions found in the local OIF PDF corpus."""

from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from protocol_pdf_diff import pdf_extract as pdf_extract_module
from protocol_pdf_diff import reporting as reporting_module
from protocol_pdf_diff.compare import (
    _covered_table_visual_row_keys,
    _last_table_caption_line,
    _mapped_parent_unique_child_rescue_pairs,
    _match_sections,
    _paragraph_review_units,
    _review_unit_key,
    _table_caption_number,
    _table_serialization_candidates,
    _table_visuals_with_cross_page_captions,
    _text_contains_table_reference,
    compare_extractions,
)
from protocol_pdf_diff.models import (
    DiffOptions,
    DiffResult,
    DocumentBlock,
    DocumentBlockKind,
    ExtractionResult,
    PageText,
    Section,
    SectionChange,
    SnippetPair,
    TableChange,
    TableRowChange,
    TableVisual,
)
from protocol_pdf_diff.pdf_extract import _table_lines_from_rows_with_coverage
from protocol_pdf_diff.reporting import (
    _build_table_changes,
    _generic_boundary_merge_patterns_for_entries,
    _make_table_row_change,
    _ordered_table_row_changes,
    _reader_change_is_coordinate_proven_table_body_duplicate,
    _reader_section_change,
    _reader_snippet_collapse_kind,
    _reader_visible_prose_tail,
    _render_single_list,
    _render_table_row_change,
    _table_cell_value_display,
    _table_row_changes,
    _table_structured_diff_kind,
    write_reports,
)


class ReaderAccuracyRegressionTests(unittest.TestCase):
    """Keep extraction artifacts out of user-visible technical differences."""

    def test_standalone_figure_caption_pair_is_absent_from_reader_changes(self) -> None:
        """Figure pixels and their split caption are source evidence, not prose differences."""

        section = Section(
            section_id="figure-caption",
            heading="Section 29.4.1.2.1 using the SSPRQ test pattern.",
            title="Section 29.4.1.2.1 using the SSPRQ test pattern.",
            level=1,
            heading_path=("Section 29.4.1.2.1 using the SSPRQ test pattern.",),
            number_path=(),
            start_page=10,
            end_page=10,
            body="Figure 29-3.\nMeasurement of VMA voltage levels for EECQ",
        )
        change = SectionChange(
            change_type="deleted",
            old_section=section,
            new_section=None,
            similarity=0.0,
            removed_snippets=[
                "Figure 29-3.",
                "Measurement of VMA voltage levels for EECQ",
            ],
        )

        self.assertIsNone(_reader_section_change(change))

    def test_figure_caption_cleanup_preserves_neighboring_normative_prose(self) -> None:
        """A real requirement remains visible when caption debris shares its section."""

        requirement = "The receiver shall meet the declared VMA limit at TP4."
        section = Section(
            section_id="mixed-figure",
            heading="1 Receiver requirement",
            title="Receiver requirement",
            level=1,
            heading_path=("1 Receiver requirement",),
            number_path=("1",),
            start_page=1,
            end_page=1,
            body=f"Figure 1-2.\nReceiver setup\n{requirement}",
        )
        change = SectionChange(
            change_type="deleted",
            old_section=section,
            new_section=None,
            similarity=0.0,
            removed_snippets=["Figure 1-2.", "Receiver setup", requirement],
        )

        cleaned = _reader_section_change(change)

        self.assertIsNotNone(cleaned)
        self.assertEqual(["Receiver setup", requirement], cleaned.removed_snippets)

    def test_figure_label_does_not_hide_an_ambiguous_technical_item(self) -> None:
        """Text shape alone cannot prove that a terse technical item belongs to a figure."""

        technical_items = [
            "Maximum differential voltage",
            "VMA = V3 - V0",
            "Voltage Modulation Amplitude (VMA)",
            "Ceeq",
        ]
        section = Section(
            section_id="ambiguous-figure-neighbor",
            heading="1 Receiver requirement",
            title="Receiver requirement",
            level=1,
            heading_path=("1 Receiver requirement",),
            number_path=("1",),
            start_page=1,
            end_page=1,
            body="\n".join(["Figure 1-2.", *technical_items]),
        )
        change = SectionChange(
            change_type="deleted",
            old_section=section,
            new_section=None,
            similarity=0.0,
            removed_snippets=["Figure 1-2.", *technical_items],
        )

        cleaned = _reader_section_change(change)

        self.assertIsNotNone(cleaned)
        self.assertEqual(technical_items, cleaned.removed_snippets)

    def test_renumbered_same_title_section_matches_without_figure_diagram_text(self) -> None:
        """Conflicting diagram labels must not split otherwise corresponding prose sections."""

        def section(number: str, body: str) -> Section:
            heading = f"{number} End-to-end linear channel description"
            return Section(
                section_id=number,
                heading=heading,
                title="End-to-end linear channel description",
                level=3,
                heading_path=(heading,),
                number_path=(number,),
                start_page=1,
                end_page=1,
                body=body,
            )

        old = section(
            "29.3.1",
            "\n".join(
                (
                    "The linear interface has normative test points to ensure interoperability between host, module and optical fiber.",
                    "Figure 29-1.",
                    "End-to-end linear channel",
                    "Host A Host B retimer retimer function Fiber function",
                    "TP1 TP1a patchcord TP2 TP3 TP4a TP4",
                    "Optical transmitter receiver module host diagram labels",
                )
            ),
        )
        new = section(
            "30.3.1",
            "\n".join(
                (
                    "The linear interface has normative test points to ensure interoperability between host, module and optical fiber.",
                    "Figure 30-1.",
                    "End to End Linear Channel",
                    "TP0 channel loss TP1a TP2 TP3 TP4a TP4 TP5",
                    "Host Tx Driver TIA Driver Host Rx Module Channel Connector",
                    "Electrical optical conversion diagram labels",
                )
            ),
        )

        matches = _match_sections([old], [new], DiffOptions())

        self.assertEqual([(0, 0)], [(old_index, new_index) for old_index, new_index, _score, _basis in matches])
        self.assertEqual("evidence_suppressed_similarity_fallback", matches[0][3])

    def test_substantive_prose_matches_despite_many_ambiguous_figure_labels(self) -> None:
        """Protected short labels stay auditable but cannot drown a long shared prose identity."""

        def section(number: str, body: str) -> Section:
            heading = f"{number} End-to-end linear channel description"
            return Section(
                section_id=number,
                heading=heading,
                title="End-to-end linear channel description",
                level=3,
                heading_path=(heading,),
                number_path=(number,),
                start_page=1,
                end_page=1,
                body=body,
            )

        shared = (
            "The linear interface has normative test points to ensure interoperability "
            "between the host, module, and optical fiber, and the receiver shall meet "
            "the declared electrical limits for every supported symbol rate."
        )
        old_labels = "\n".join(f"Legacy host label {index}" for index in range(30))
        new_labels = "\n".join(f"Revised module label {index}" for index in range(30))
        old = section("29.3.1", f"{shared}\nFigure 29-1.\n{old_labels}")
        new = section("30.3.1", f"{shared}\nFigure 30-1.\n{new_labels}")

        matches = _match_sections([old], [new], DiffOptions())

        self.assertEqual((0, 0), matches[0][:2])
        self.assertEqual("evidence_suppressed_similarity_fallback", matches[0][3])

    def test_matching_ignores_combined_figure_caption_and_diagram_ocr(self) -> None:
        """One caption+diagram unit may be dropped for identity without hiding it from audit."""

        def section(number: str, body: str) -> Section:
            heading = f"{number} Module output test (TP3 to TP4)"
            return Section(
                section_id=number,
                heading=heading,
                title="Module output test (TP3 to TP4)",
                level=4,
                heading_path=(heading,),
                number_path=(number,),
                start_page=1,
                end_page=1,
                body=body,
            )

        common = (
            "The module output EECQ is tested at TP4 using a Module Compliance Board."
        )
        old = section(
            "29.4.1.2",
            common
            + "\nFigure 29-9. Module output test setup Host A Fiber TP3 TP4 "
            + "legacy optical diagram label " * 35,
        )
        new = section(
            "30.4.1.2",
            common
            + "\nFigure 30-9. Module output test setup Driver TIA TP3 TP4 "
            + "revised electrical diagram label " * 35,
        )

        matches = _match_sections([old], [new], DiffOptions())

        self.assertEqual((0, 0), matches[0][:2])
        self.assertEqual("evidence_suppressed_similarity_fallback", matches[0][3])

    def test_unique_title_and_strong_body_survive_polluted_parent_hierarchy(self) -> None:
        """A wrong extracted ancestor cannot split one uniquely titled, strongly related clause."""

        old = Section(
            "old-module",
            "29.4.1.2 Module output test (TP3 to TP4)",
            "Module output test (TP3 to TP4)",
            5,
            (
                "Appendix 16.D.",
                "Section 29.3.12). The signals at the appropriate points in the reference receiver (see",
                "29.4.1.2 Module output test (TP3 to TP4)",
            ),
            ("Appendix 16", "Section 29.3", "29.4.1.2"),
            17,
            17,
            "The module output EECQ is tested at TP4 of Figure 25-1 using a Module Compliance Board as defined in Section 25.3.1. The test setup is shown in Figure 29-9.",
        )
        new = Section(
            "new-module",
            "30.4.1.2 Module output test (TP3 to TP4)",
            "Module output test (TP3 to TP4)",
            4,
            (
                "1 All co-propagating and counter-propagating lanes are active as crosstalk sources,",
                "30.4.1.2 Module output test (TP3 to TP4)",
            ),
            ("1", "30.4.1.2"),
            19,
            19,
            "The module output EECQ is tested at TP4 of Figure 25-1 using a Module Compliance Board as defined in IEEE P802.3dj Clause 179B. The test setup is shown in Figure 30-9.",
        )

        old_child = Section(
            "old-method",
            "29.4.1.2.1 Module output test method",
            "Module output test method",
            6,
            (*old.heading_path, "29.4.1.2.1 Module output test method"),
            (*old.number_path, "29.4.1.2.1"),
            17,
            19,
            "Legacy SSPRQ capture, 42 GHz receiver, 22-tap FFE, and module calibration procedure. "
            * 12,
        )
        new_child = Section(
            "new-method",
            "30.4.1.2.1 Module output test method",
            "Module output test method",
            5,
            (*new.heading_path, "30.4.1.2.1 Module output test method"),
            (*new.number_path, "30.4.1.2.1"),
            19,
            21,
            "Revised 60 GHz receiver, 30-tap FFE, DFE constraints, and host channel procedure. "
            * 12,
        )

        matches = _match_sections([old, old_child], [new, new_child], DiffOptions())

        self.assertEqual((0, 0), matches[0][:2])
        self.assertEqual("unique_title_body_fallback", matches[0][3])
        child_match = next(match for match in matches if match[:2] == (1, 1))
        self.assertEqual("structural_mapped_parent_unique_child", child_match[3])

    def test_duplicate_parent_path_cannot_redirect_a_unique_child_rescue(self) -> None:
        """Repeated extracted paths must fail closed instead of overwriting parent identity."""

        def section(
            section_id: str,
            title: str,
            level: int,
            number_path: tuple[str, ...],
        ) -> Section:
            return Section(
                section_id=section_id,
                heading=f"{' '.join(number_path)} {title}",
                title=title,
                level=level,
                heading_path=(title,),
                number_path=number_path,
                start_page=1,
                end_page=1,
                body=f"{title} shall preserve its own parent identity.",
            )

        old_sections = [
            section("old-alpha", "Alpha parent", 1, ("1",)),
            section("old-method", "Method", 2, ("1", "1.1")),
            section("old-beta", "Beta parent", 1, ("1",)),
        ]
        new_sections = [
            section("new-alpha", "Alpha parent", 1, ("10",)),
            section("new-beta", "Beta parent", 1, ("20",)),
            section("new-method", "Method", 2, ("20", "20.1")),
        ]
        matches = [
            (0, 0, 0.9, "unique_title_body_fallback"),
            (2, 1, 0.9, "unique_title_body_fallback"),
        ]

        rescued = _mapped_parent_unique_child_rescue_pairs(
            old_sections,
            new_sections,
            matches,
            {0, 2},
            {0, 1},
        )

        self.assertEqual([], rescued)

    def test_exact_table_reconciliation_transposes_only_codec_newlines(self) -> None:
        """A merged header/data row is recoverable without treating `/` as layout."""

        rows, _fully_represented = _table_lines_from_rows_with_coverage(
            [
                ["ic_req\npreset 1", "SNDR (min)\n33.5", "Units\ndB"],
                ["preset 2", "27.5", "dB"],
            ],
            1,
        )
        candidates = _table_serialization_candidates(
            "Table 31-9. SNDR Limits",
            rows,
        )

        self.assertIn(r"Column 1=ic_req\npreset 1", rows[0])
        self.assertIn(
            (
                "Table 31-9. SNDR Limits ic_req SNDR (min) Units "
                "preset 1 33.5 dB preset 2 27.5 dB"
            ),
            candidates,
        )

        literal_slash_rows = [
            "表格行: T1 | Column 1=ic_req / preset 1 | "
            "Column 2=SNDR (min) / 33.5 | Column 3=Units / dB"
        ]
        literal_candidates = _table_serialization_candidates(
            "Table 31-9. SNDR Limits",
            literal_slash_rows,
        )
        self.assertNotIn(candidates[-1], literal_candidates)

    def test_exact_table_reconciliation_accepts_coordinate_suppressed_source_caption(
        self,
    ) -> None:
        """A source visual remains authoritative when its caption left the body by bbox proof."""

        rows, _fully_represented = _table_lines_from_rows_with_coverage(
            [
                ["ic_req\npreset 1", "SNDR (min)\n33.5", "Units\ndB"],
                ["preset 2", "27.5", "dB"],
            ],
            1,
        )
        caption = "Table 31-9. SNDR Limits"
        serialized = (
            "Table 31-9. SNDR Limits ic_req SNDR (min) Units "
            "preset 1 33.5 dB preset 2 27.5 dB"
        )
        old = ExtractionResult(
            pdf_path=Path("old-flat-table.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{serialized}")],
            total_pages=1,
        )
        new = ExtractionResult(
            pdf_path=Path("new-coordinate-table.pdf"),
            pages=[PageText(page_number=1, text="1 Scope\n" + "\n".join(rows))],
            table_visuals=[
                TableVisual(
                    page_number=1,
                    table_number=1,
                    title=caption,
                    bbox=(10.0, 20.0, 300.0, 160.0),
                    image_data_uri="",
                    row_texts=rows,
                    grid_summary="structured rows",
                )
            ],
            total_pages=1,
        )

        result = compare_extractions(old, new, DiffOptions())
        rebuilt = [
            table
            for table in result.old_table_visuals
            if table.title == caption
            and table.ocr_status == "text_backed_exact_match"
        ]
        visible_text = "\n".join(
            [
                snippet
                for change in result.changes
                for snippet in (*change.added_snippets, *change.removed_snippets)
            ]
            + [
                text
                for change in result.changes
                for pair in change.replaced_snippets
                for text in (pair.old, pair.new)
            ]
        )

        self.assertEqual(1, len(rebuilt))
        self.assertNotIn("SNDR Limits ic_req", visible_text)
        matched = [
            change
            for change in _build_table_changes(result)
            if any(
                table.title == caption
                for table in (*change.old_tables, *change.new_tables)
            )
        ]
        self.assertEqual(1, len(matched))
        self.assertEqual("review", matched[0].change_type)

    def test_transposed_multiline_candidate_rejects_equal_length_prose_wraps(self) -> None:
        """Equal physical line counts alone cannot turn prose into logical rows."""

        rows, _fully_represented = _table_lines_from_rows_with_coverage(
            [
                [
                    "Maximum\nallowed\noutput",
                    "At low\ntest\nfrequency",
                ],
                ["Stable", "unchanged"],
            ],
            1,
        )
        candidates = _table_serialization_candidates(
            "Table 1-1. Wrapped prose",
            rows,
        )

        self.assertNotIn(
            (
                "Table 1-1. Wrapped prose Maximum At low allowed test "
                "output frequency Stable unchanged"
            ),
            candidates,
        )

    def test_transposed_multiline_candidate_rejects_prose_that_merely_contains_digits(self) -> None:
        """Digits inside prose do not prove that a merged cell contains table records."""

        rows, _fully_represented = _table_lines_from_rows_with_coverage(
            [
                [
                    "Parameter\nlimit 1",
                    "Value\nat 2 GHz",
                    "Units\nper 3 lanes",
                ],
                ["Stable", "unchanged", "text"],
            ],
            1,
        )
        candidates = _table_serialization_candidates(
            "Table 1-1. Wrapped prose with digits",
            rows,
        )

        self.assertNotIn(
            (
                "Table 1-1. Wrapped prose with digits Parameter Value Units "
                "limit 1 at 2 GHz per 3 lanes Stable unchanged text"
            ),
            candidates,
        )

    def test_exact_table_reconciliation_rejects_multiword_cell_boundary_collisions(self) -> None:
        """Flat raw text cannot distinguish words reassigned across physical columns."""

        old_rows = [
            "表格行: T1 | Column 1=Alpha Beta | Column 2=Gamma",
            "表格行: T1 | Column 1=Delta | Column 2=Epsilon",
        ]
        new_rows = [
            "表格行: T1 | Column 1=Alpha | Column 2=Beta Gamma",
            "表格行: T1 | Column 1=Delta | Column 2=Epsilon",
        ]
        caption = "Table 1-1. Cell boundary evidence"

        self.assertEqual([], _table_serialization_candidates(caption, old_rows))
        self.assertEqual([], _table_serialization_candidates(caption, new_rows))

        old_table = TableVisual(
            1,
            1,
            caption,
            (0.0, 0.0, 100.0, 100.0),
            "",
            old_rows,
            "grid",
        )
        flattened_text = f"1 Scope\n{caption}\nAlpha Beta Gamma\nDelta Epsilon"
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-cell-boundary.pdf"),
                pages=[PageText(page_number=1, text=flattened_text)],
                table_visuals=[old_table],
            ),
            ExtractionResult(
                pdf_path=Path("new-cell-boundary.pdf"),
                pages=[PageText(page_number=1, text=flattened_text)],
            ),
            DiffOptions(),
        )

        self.assertFalse(
            any(
                table.ocr_status == "text_backed_exact_match"
                for table in result.new_table_visuals
            )
        )
        self.assertTrue(_build_table_changes(result))

    def test_flat_values_cannot_hide_caption_or_row_column_shape_changes(self) -> None:
        """Atomic tokens still need visible schema; flattening alone has no suppression power."""

        title_collision_row = ["表格行: T1 | Value=B"]
        shape_collision_row = [
            "表格行: T1 | Column 1=A | Column 2=B | Column 3=C | Column 4=D"
        ]
        self.assertNotIn(
            "Table 1. Foo A B",
            _table_serialization_candidates("Table 1. Foo A", title_collision_row),
        )  # 只有包含可见 `Value` 表头的候选存在，不能命中旧侧无表头原文。
        self.assertEqual(
            [],
            _table_serialization_candidates("Table 1. Shape", shape_collision_row),
        )

        for title, row, raw_text in (
            ("Table 1. Foo A", title_collision_row, "Table 1.\nFoo\nA B"),
            ("Table 1. Shape", shape_collision_row, "Table 1. Shape\nA B\nC D"),
        ):
            with self.subTest(title=title):
                table = TableVisual(
                    1,
                    1,
                    title,
                    (0.0, 0.0, 100.0, 100.0),
                    "",
                    row,
                    "grid",
                )
                result = compare_extractions(
                    ExtractionResult(
                        pdf_path=Path("old-flat-shape.pdf"),
                        pages=[PageText(page_number=1, text=f"1 Scope\n{raw_text}")],
                    ),
                    ExtractionResult(
                        pdf_path=Path("new-flat-shape.pdf"),
                        pages=[PageText(page_number=1, text=f"1 Scope\n{title}")],
                        table_visuals=[table],
                    ),
                    DiffOptions(),
                )
                self.assertFalse(
                    any(
                        visual.ocr_status == "text_backed_exact_match"
                        for visual in result.old_table_visuals
                    )
                )
                self.assertTrue(result.changes or _build_table_changes(result))

    def test_generated_header_numeric_records_have_a_controlled_reconciliation_candidate(self) -> None:
        """A visible header plus categorical rows and fixed numeric suffixes is reviewable."""

        rows = [
            (
                "表格行: T1 | Column 1=Material Class | "
                "Column 2=Attenuation per mm (dB/mm) | Column 3=Reach at 12 dB (mm)"
            ),
            "表格行: T1 | Column 1=Medium Loss PCB | Column 2=0.1 | Column 3=120",
            "表格行: T1 | Column 1=Low Loss PCB | Column 2=0.08 | Column 3=150",
        ]

        self.assertIn(
            (
                "Table 1. Reach Material Class Attenuation per mm (dB/mm) "
                "Reach at 12 dB (mm) Medium Loss PCB 0.1 120 Low Loss PCB 0.08 150"
            ),
            _table_serialization_candidates("Table 1. Reach", rows),
        )

    def test_exact_table_reconciliation_rejects_unobservable_empty_cells(self) -> None:
        """Raw text cannot prove the position or existence of an empty column."""

        rows = [
            "表格行: T1 | Column 1=A | Column 2= | Column 3=B",
            "表格行: T1 | Column 1=C | Column 2= | Column 3=D",
        ]

        self.assertEqual(
            [],
            _table_serialization_candidates("Table 1-1. Empty field", rows),
        )

    def test_plain_uppercase_words_do_not_authorize_table_row_expansion(self) -> None:
        """A Symbol header does not make ordinary uppercase prose an identifier."""

        rows = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value"],
                ["First\nSecond", "MAXIMUM\nOUTPUT", "1\n2"],
            ],
            1,
        )

        self.assertEqual(1, len(rows))
        self.assertIn("Symbol=MAXIMUM / OUTPUT", rows[0])

    def test_two_letter_words_do_not_authorize_table_row_expansion(self) -> None:
        """Without geometry, IN/AT can be wrapped prose rather than two symbols."""

        rows = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value"],
                ["First\nSecond", "IN\nAT", "1\n2"],
            ],
            1,
        )

        self.assertEqual(1, len(rows))
        self.assertIn("Symbol=IN / AT", rows[0])

    def test_two_numeric_columns_prove_rows_but_formula_continuations_do_not(self) -> None:
        """Independent Min/Max records expand; `1\n+2` stays one uncertain formula."""

        expanded = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Min", "Max"],
                ["Setting A\nSetting B", "1\n2", "10\n20"],
            ],
            1,
        )
        uncertain_formula = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Min", "Max"],
                ["Limit", "1\n+2", "10\n20"],
            ],
            1,
        )

        self.assertEqual(2, len(expanded))
        self.assertIn("Parameter=Setting A | Min=1 | Max=10", expanded[0])
        self.assertIn("Parameter=Setting B | Min=2 | Max=20", expanded[1])
        self.assertEqual(1, len(uncertain_formula))
        self.assertIn(r"Min=1\n+2", uncertain_formula[0])

        for rows in (
            [
                ["Parameter", "Min", "Typ", "Max"],
                ["Limit", "1 +\n2", "10\n20", "30\n40"],
            ],
            [
                ["Parameter", "Symbol", "Min", "Max", "Units"],
                ["First\nSecond", "R1\nR2", "1\n+2", "10\n20", "V\nV"],
            ],
            [["Limit", "1\n+2", "10\n20", "30\n40"]],
        ):
            with self.subTest(rows=rows):
                lines = pdf_extract_module._table_lines_from_rows(rows, 1)
                self.assertEqual(1, len(lines))
                self.assertNotIn("Min=+2", "\n".join(lines))
                self.assertNotIn("Column 2=+2", "\n".join(lines))

        for wrapped_formula in (
            "1 ±\n2",
            "1\n±2",
            "1 =\n2",
            "1 ^\n2",
            "1 ·\n2",
            "1 ⋅\n2",
        ):
            with self.subTest(wrapped_formula=wrapped_formula):
                lines = pdf_extract_module._table_lines_from_rows(
                    [
                        ["Parameter", "Min", "Typ", "Max"],
                        ["Limit", wrapped_formula, "10\n20", "30\n40"],
                    ],
                    1,
                )
                self.assertEqual(1, len(lines))

    def test_wrapped_condition_is_not_split_by_neighboring_numeric_columns(self) -> None:
        """Two numeric columns cannot turn `and above` into a second record condition."""

        for condition in (
            "At 1 GHz\nand above",
            "Measured at TP1\nusing the fixture",
            "Valid in this mode\nper Note 1",
            "Required for operation\nat 1 GHz",
            "Use the value\nspecified in Note 1",
            "Applied at TP1,\n2 GHz bandwidth",
            "x = 1\n+2",
        ):
            with self.subTest(condition=condition):
                lines = pdf_extract_module._table_lines_from_rows(
                    [
                        ["Parameter", "Condition", "Min", "Max"],
                        [
                            "Setting A\nSetting B",
                            condition,
                            "1\n2",
                            "10\n20",
                        ],
                    ],
                    1,
                )
                self.assertEqual(1, len(lines))

    def test_each_multiline_symbol_unit_and_unknown_column_needs_own_record_proof(self) -> None:
        """Neighboring Min/Max columns cannot split JH4u, units, or free prose."""

        cases = (
            ("Symbol", "JH\n4u"),
            ("Symbol", "JRMS\n03"),
            ("Symbol", "MAXIMUM\nOUTPUT"),
            ("Units", "m\nV"),
            ("Units", "ns/\nmm"),
            ("Units", "UI\nrms"),
            ("Test Point", "Measured at TP1\nusing fixture A"),
            ("Value", "Equation\n(31-1)"),
            ("Value", "Maximum\nOutput"),
        )
        for label, wrapped in cases:
            with self.subTest(label=label, wrapped=wrapped):
                lines = pdf_extract_module._table_lines_from_rows(
                    [
                        ["Parameter", label, "Min", "Max"],
                        ["Setting A\nSetting B", wrapped, "1\n2", "10\n20"],
                    ],
                    1,
                )
                self.assertEqual(1, len(lines))

    def test_descriptor_soft_wraps_do_not_become_extra_parameter_rows(self) -> None:
        """Known phrases and hyphenated words remain one cell despite numeric neighbors."""

        for descriptor in (
            "maximum\nlikelihood",
            "Maximum\nOutput",
            "Uncorrelated\nJitter",
            "peak-to-\npeak",
            "Reference\nclock",
            "Random\njitter",
            "Target\ndetector",
            "Channel\noperating",
            "Voltage\ntolerance",
            "Noise\npower",
            "Time\ninterval",
        ):
            with self.subTest(descriptor=descriptor):
                lines = pdf_extract_module._table_lines_from_rows(
                    [
                        ["Parameter", "Min", "Max"],
                        [descriptor, "1\n2", "10\n20"],
                    ],
                    1,
                )

                self.assertEqual(1, len(lines))
                self.assertIn(descriptor.replace("\n", r"\n"), lines[0])

        spanning_descriptor = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Min", "Max"],
                ["Reference clock", "1\n2", "10\n20"],
            ],
            1,
        )
        self.assertEqual(1, len(spanning_descriptor))

    def test_symbol_and_numeric_grid_cannot_override_descriptor_soft_wraps(self) -> None:
        """Strong neighboring cells still cannot split a proven wrapped phrase."""

        for descriptor in (
            "maximum\nlikelihood",
            "The maximum\nlikelihood",
            "Maximum\nOutput",
            "peak-to-\npeak",
            "Sinusoidal jitter, peak-to\npeak",
            "Equation\n(31-1)",
            "The maximum likelihood\nsequence detection",
            "Sinusoidal jitter,\npeak-to-peak (UI)",
            "Uncorrelated jitter RMS (standard deviation of\nthe probability distribution)",
            "Uncorrelated Jitter (time interval from 0.0025% to\n99.9975% of the probability distribution)",
            "Reference clock\nphase noise",
            "Target detector\nerror ratio",
            "Channel operating\nmargin, min",
            "Noise power spectral\ndensity",
            "Time interval\nerror",
            "Random jitter\nRMS",
            "Voltage tolerance\nmaximum",
            "Standard deviation of\nthe probability distribution",
            "The maximum likelihood sequence detection\n(MLSD)",
            "Sinusoidal jitter, peak-to-peak\n(UI)",
            "Uncorrelated jitter RMS\n(standard deviation of the probability distribution)",
            "Target detector error\nratio",
            "Channel operating margin,\nmin",
            "Noise power spectral density\n(V2/GHz)",
            "Time interval error\n(TIE)",
            "Reference clock phase\nnoise",
            "Voltage tolerance, maximum\n(mV)",
            "Standard deviation of the probability\ndistribution",
            "Uncorrelated\nJitter",
            "Characteristic\nimpedance",
            "Transmission\nline",
            "Sinusoidal\njitter",
            "Reference\nclock",
            "Random\njitter",
            "Target\ndetector",
            "Channel\noperating",
            "Voltage\ntolerance",
            "Noise\npower",
            "Time\ninterval",
        ):
            with self.subTest(descriptor=descriptor):
                lines = pdf_extract_module._table_lines_from_rows(
                    [
                        ["Parameter", "Symbol", "Value"],
                        [descriptor, "L\nC", "1\n2"],
                    ],
                    1,
                )

                self.assertEqual(1, len(lines))
                self.assertIn(descriptor.replace("\n", r"\n"), lines[0])

    def test_complete_descriptor_rows_containing_soft_wrap_terms_still_expand(self) -> None:
        """A phrase inside one complete row cannot suppress a real row boundary."""

        descriptor_pairs = (
            ("Target detector error ratio", "Channel operating margin"),
            ("Maximum likelihood value", "Maximum output value"),
            ("Reference clock amplitude", "Random jitter RMS"),
            ("Transmission line length", "Characteristic impedance"),
            ("Noise power spectral density", "Time interval error"),
            ("Uncorrelated jitter", "Correlated jitter"),
            ("Low-frequency limit", "f > 10 GHz"),
            ("Receiver limit", "10G mode limit"),
            ("Preset mode 1", "mode 2"),
            ("Channel profile A", "profile B"),
            ("Output state A", "disabled state"),
            ("Lane setting one", "z-axis limit"),
            ("Maximum value", "(reserved)"),
            ("Target detector setting", "error ratio limit"),
            ("Reference clock amplitude", "phase noise limit"),
            ("Maximum likelihood value", "sequence detection enabled"),
            ("Noise power spectral mask", "density profile"),
            ("Time interval setting", "error mode"),
            ("Random jitter limit", "RMS mode"),
            ("Receiver maximum", "output power"),
            ("Input characteristic", "impedance minimum"),
            ("Clock reference", "clock drift"),
            ("Measured time", "interval count"),
            ("Total noise", "power limit"),
            ("Mode channel", "operating state"),
            ("Declared voltage", "tolerance profile"),
            ("Reported random", "jitter limit"),
        )
        for first, second in descriptor_pairs:
            with self.subTest(descriptors=(first, second)):
                rows = [
                    ["Parameter", "Symbol", "Value", "Units"],
                    [
                        f"{first}\n{second}",
                        "P1\nP2",
                        "10\n20",
                        "UI\nUI",
                    ],
                ]

                def word(text: str, top: float) -> dict[str, object]:
                    return {
                        "text": text,
                        "x0": 0.0,
                        "x1": float(max(len(text), 1) * 6),
                        "top": top,
                        "bottom": top + 10.0,
                        "size": 10.0,
                    }

                cell_words = [
                    [[], [], [], []],
                    [
                        [word(first, 100.0), word(second, 120.0)],
                        [word("P1", 100.0), word("P2", 120.0)],
                        [word("10", 100.0), word("20", 120.0)],
                        [word("UI", 100.0), word("UI", 120.0)],
                    ],
                ]
                lines, content_lossless, alignment_reliable = (
                    pdf_extract_module._table_lines_from_rows_with_evidence(
                        rows,
                        1,
                        cell_word_rows=cell_words,
                    )
                )

                self.assertEqual(2, len(lines))
                self.assertTrue(content_lossless)
                self.assertTrue(alignment_reliable)
                self.assertIn(f"Parameter={first}", lines[0])
                self.assertIn(f"Parameter={second}", lines[1])

    def test_open_vocabulary_descriptor_reflow_is_never_a_material_change_wall(self) -> None:
        """Unknown technical phrases may rewrap without becoming delete/add walls."""

        old_rows = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value", "Units"],
                ["Receiver input\nreturn loss", "S11\nS22", "1\n2", "dB\ndB"],
            ],
            1,
        )
        new_rows = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value", "Units"],
                ["Receiver input return\nloss", "S11\nS22", "1\n2", "dB\ndB"],
            ],
            1,
        )

        def table(rows: list[str]) -> TableVisual:
            return TableVisual(
                page_number=1,
                table_number=1,
                title="Table 1. Receiver limits",
                bbox=(0.0, 0.0, 100.0, 100.0),
                image_data_uri="",
                row_texts=rows,
                grid_summary="",
            )

        changes = _table_row_changes((table(old_rows),), (table(new_rows),))

        self.assertEqual(2, len(old_rows))
        self.assertEqual(2, len(new_rows))
        self.assertEqual(1, len(changes))
        self.assertEqual("需人工复核", changes[0].change_type)

        def word(text: str, top: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": 0.0,
                "x1": float(max(len(text), 1) * 6),
                "top": top,
                "bottom": top + 10.0,
                "size": 10.0,
            }

        def expanded(descriptor_lines: tuple[str, str]) -> list[str]:
            rows = [
                ["Parameter", "Symbol", "Value", "Units"],
                ["\n".join(descriptor_lines), "S11\nS22", "1\n2", "dB\ndB"],
            ]
            cell_words = [
                [[], [], [], []],
                [
                    [word(descriptor_lines[0], 100.0), word(descriptor_lines[1], 120.0)],
                    [word("S11", 100.0), word("S22", 120.0)],
                    [word("1", 100.0), word("2", 120.0)],
                    [word("dB", 100.0), word("dB", 120.0)],
                ],
            ]
            lines, _content_lossless, alignment_reliable = (
                pdf_extract_module._table_lines_from_rows_with_evidence(
                    rows,
                    1,
                    cell_word_rows=cell_words,
                )
            )
            self.assertTrue(alignment_reliable)
            return lines

        old_expanded = expanded(("Receiver input", "return loss"))
        new_expanded = expanded(("Receiver input return", "loss"))
        review_changes = _table_row_changes(
            (table(old_expanded),),
            (table(new_expanded),),
        )

        self.assertEqual(2, len(old_expanded))
        self.assertEqual(2, len(new_expanded))
        self.assertEqual(1, len(review_changes))
        self.assertEqual("需人工复核", review_changes[0].change_type)

    def test_symbol_fragments_do_not_become_rows_even_with_min_max(self) -> None:
        """A compact symbol split by PDF layout cannot borrow row proof from Min/Max."""

        for symbol in (
            "Zc\n2",
            "JH4\nu",
            "JRMS0\n3",
            "EOJ0\n3",
            "T_JH4\n.3u",
            "Cd(\n1)",
            "J\nH4u",
            "T_\nJH4u",
        ):
            with self.subTest(symbol=symbol):
                lines = pdf_extract_module._table_lines_from_rows(
                    [
                        ["Parameter", "Symbol", "Min", "Max"],
                        ["Setting A\nSetting B", symbol, "1\n2", "10\n20"],
                    ],
                    1,
                )

                self.assertEqual(1, len(lines))

        numbered_symbols = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value", "Units"],
                ["Line 2\nLine 3", "zp2\nzp3", "2\n3", "mm\nmm"],
            ],
            1,
        )
        self.assertEqual(2, len(numbered_symbols))
        self.assertIn("Symbol=zp2", numbered_symbols[0])
        self.assertIn("Symbol=zp3", numbered_symbols[1])

        comma_list_values = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value", "Units"],
                [
                    "Line 2\nLine 3",
                    "zp2\nzp3",
                    "44,45\n29,30",
                    "mm\nmm",
                ],
            ],
            1,
        )
        self.assertEqual(2, len(comma_list_values))
        self.assertIn("Value=44,45", comma_list_values[0])
        self.assertIn("Value=29,30", comma_list_values[1])

    def test_common_units_and_enums_expand_only_with_complete_record_evidence(self) -> None:
        """Common protocol units/enums remain readable when other typed columns align."""

        for unit in (
            "mA",
            "A",
            "mW",
            "dBm",
            "dBc",
            "Gb/s",
            "GBd",
            "pF",
            "nH",
            "ppm",
            "°C",
        ):
            with self.subTest(unit=unit):
                lines = pdf_extract_module._table_lines_from_rows(
                    [
                        ["Parameter", "Symbol", "Value", "Units"],
                        [
                            "Preset 1\nPreset 2",
                            "R1\nR2",
                            "10\n20",
                            f"{unit}\n{unit}",
                        ],
                    ],
                    1,
                )
                self.assertEqual(2, len(lines))

        for old_value, new_value in (
            ("Yes", "No"),
            ("Open", "Closed"),
            ("Enabled", "Disabled"),
            ("Required", "Optional"),
            ("NRZ", "PAM4"),
            ("Pass", "Fail"),
        ):
            with self.subTest(old_value=old_value, new_value=new_value):
                lines = pdf_extract_module._table_lines_from_rows(
                    [
                        ["Parameter", "Symbol", "Value", "Units"],
                        [
                            "Setting A\nSetting B",
                            "R1\nR2",
                            f"{old_value}\n{new_value}",
                            "dB\ndB",
                        ],
                    ],
                    1,
                )
                self.assertEqual(2, len(lines))

    def test_complete_protocol_symbols_need_independent_numeric_row_evidence(self) -> None:
        """RX/TX-like tokens may align with Min/Max but not with one Value column alone."""

        for symbols in (
            "RX\nTX",
            "COM\nSNDR",
            "EOJ\nJRMS",
            "FFE\nDFE",
            "DCD\nISI",
        ):
            with self.subTest(symbols=symbols):
                expanded = pdf_extract_module._table_lines_from_rows(
                    [
                        ["Parameter", "Symbol", "Min", "Max"],
                        ["Metric A\nMetric B", symbols, "1\n2", "10\n20"],
                    ],
                    1,
                )
                self.assertEqual(2, len(expanded))

        ambiguous = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Value"],
                ["Receiver\nTransmitter", "RX\nTX", "1\n2"],
            ],
            1,
        )
        ordinary_words = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Symbol", "Min", "Max"],
                ["Setting A\nSetting B", "IN\nAT", "1\n2", "10\n20"],
            ],
            1,
        )

        self.assertEqual(1, len(ambiguous))
        self.assertEqual(1, len(ordinary_words))

        amplitude_source = [
            ["Parameter", "Symbol", "Value", "Units"],
            [
                "Victim\nFar-end aggressor\nNear-end aggressor",
                "Av\nAfe\nAne",
                "0.385\n0.385\n0.481",
                "V\nV\nV",
            ],
        ]
        self.assertEqual(
            3,
            len(pdf_extract_module._table_lines_from_rows(amplitude_source, 1)),
        )  # 描述、符号、数值、单位四列共同自证三条记录；不能因缺坐标压成一行。

        def word(text: str, top: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": 0.0,
                "x1": float(max(len(text), 1) * 6),
                "top": top,
                "bottom": top + 10.0,
                "size": 10.0,
            }

        tops = (100.0, 120.0, 140.0)
        amplitude_words = [
            [[], [], [], []],
            [
                [
                    word("Victim", tops[0]),
                    word("Far-end aggressor", tops[1]),
                    word("Near-end aggressor", tops[2]),
                ],
                [word(value, top) for value, top in zip(("Av", "Afe", "Ane"), tops)],
                [
                    word(value, top)
                    for value, top in zip(("0.385", "0.385", "0.481"), tops)
                ],
                [word("V", top) for top in tops],
            ],
        ]
        amplitude_rows, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                amplitude_source,
                1,
                cell_word_rows=amplitude_words,
            )
        )
        self.assertEqual(3, len(amplitude_rows))
        self.assertTrue(content_lossless)
        self.assertTrue(alignment_reliable)
        self.assertIn("Symbol=Afe", amplitude_rows[1])
        self.assertIn("Symbol=Ane", amplitude_rows[2])

    def test_grouped_jitter_rows_expand_with_repeated_missing_minimums(self) -> None:
        """Repeated '-' placeholders are rows, not a wrapped subtraction formula."""

        lines = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Min", "Max", "Unit", "Conditions"],
                [
                    "Output Jitter\nJRMS\nEOJ03\nJH4u",
                    "-\n-\n-",
                    "0.023\n0.025\n0.118",
                    "UI\nUI\nUI",
                    "See 31.3.13",
                ],
            ],
            1,
        )

        self.assertEqual(3, len(lines))
        self.assertIn(r"Parameter=Output Jitter\nJRMS | Min=- | Max=0.023", lines[0])
        self.assertIn("Parameter=EOJ03 | Min=- | Max=0.025", lines[1])
        self.assertIn("Parameter=JH4u | Min=- | Max=0.118", lines[2])

    def test_group_label_does_not_turn_uppercase_prose_into_identifiers(self) -> None:
        """A leading description cannot make MAXIMUM/LIKELIHOOD look like two records."""

        for descriptor in (
            "Detector definition\nMAXIMUM\nLIKELIHOOD",
            "Receiver requirements\nMAXIMUM\nOUTPUT",
        ):
            with self.subTest(descriptor=descriptor):
                lines = pdf_extract_module._table_lines_from_rows(
                    [
                        ["Parameter", "Min", "Max", "Unit"],
                        [descriptor, "-\n-", "0.5\n0.6", "UI\nUI"],
                    ],
                    1,
                )

                self.assertEqual(1, len(lines))

    def test_mixed_missing_and_numeric_values_are_records_not_subtraction(self) -> None:
        """A standalone '-' is a missing cell; a signed number remains arithmetic evidence."""

        for minimum in ("-\n0.1", "0.1\n-"):
            with self.subTest(minimum=minimum):
                lines = pdf_extract_module._table_lines_from_rows(
                    [
                        ["Parameter", "Min", "Max", "Unit"],
                        ["Limit A\nLimit B", minimum, "0.2\n0.3", "UI\nUI"],
                    ],
                    1,
                )
                self.assertEqual(2, len(lines))

        signed_continuation = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Min", "Max", "Unit"],
                ["Limit A\nLimit B", "1\n-2", "0.2\n0.3", "UI\nUI"],
            ],
            1,
        )
        self.assertEqual(1, len(signed_continuation))

    def test_grouped_parameter_row_expands_with_pua_greek_symbols(self) -> None:
        """A group label and split Symbol-font subscripts must not shift values."""

        rows = [
            ["Parameter", "Symbol", "Value", "Units"],
            [
                (
                    "Device package model: Class B\n"
                    "Transmission line characteristic impedance\n"
                    "Transmission line parameter\n"
                    "Transmission line parameter\n"
                    "Transmission line parameter"
                ),
                "Z\nc\n\uf067\n0\n\uf074\na\n1",
                "87.5\n5.0×10-4\n6.141×10-4\n6.5×10-4",
                "Ω\n1/mm\nns/mm\nns1/2/mm",
            ],
        ]

        lines, fully_represented = _table_lines_from_rows_with_coverage(rows, 1)

        self.assertTrue(fully_represented)
        self.assertEqual(4, len(lines))
        self.assertIn("Parameter=Device package model: Class B\\nTransmission line characteristic impedance", lines[0])
        self.assertIn("Symbol=Zc", lines[0])
        self.assertIn("Value=87.5", lines[0])
        self.assertIn("Symbol=\uf0670", lines[1])
        self.assertIn("Value=5.0×10-4", lines[1])
        self.assertIn("Symbol=\uf074", lines[2])
        self.assertIn("Value=6.141×10-4", lines[2])
        self.assertIn("Symbol=a1", lines[3])
        self.assertIn("Value=6.5×10-4", lines[3])

    def test_table_cell_audit_value_preserves_known_symbol_font_pua(self) -> None:
        """The shared table fact stays raw until a reader renderer is selected."""

        self.assertEqual(
            "\uf0610 / \uf0670 / \uf073 / \uf074 / \uf0a3 / \uf0a4 / \uf02c / \uf03c",
            _table_cell_value_display(
                "\uf0610 / \uf0670 / \uf073 / \uf074 / \uf0a3 / \uf0a4 / \uf02c / \uf03c"
            ),
        )

    def test_symbol_spacing_is_ignored_only_inside_compact_symbol_fields(self) -> None:
        """Extraction spacing in Zc/Zc2 is noise, while ordinary words stay distinct."""

        for old_symbol, new_symbol in (
            ("Z c", "Zc"),
            ("Z c2", "Zc2"),
            ("z c", "zc"),
            ("z p", "zp"),
            ("f b", "fb"),
            ("JH 4u", "JH4u"),
            ("EOJ 03", "EOJ03"),
            ("JRMS 03", "JRMS03"),
            ("T_JH 4.3u", "T_JH4.3u"),
        ):
            with self.subTest(symbol=(old_symbol, new_symbol)):
                old_row = (
                    f"表格行: T1 | Parameter=Impedance | Symbol={old_symbol} | Value=87.5"
                )
                new_row = (
                    f"表格行: T1 | Parameter=Impedance | Symbol={new_symbol} | Value=87.5"
                )
                self.assertEqual(
                    "无变化",
                    _table_structured_diff_kind(old_row, new_row),
                )
                self.assertEqual([], _ordered_table_row_changes([old_row], [new_row]))
        self.assertNotEqual(
            "无变化",
            _table_structured_diff_kind(
                "表格行: T1 | Parameter=Mode | Value=A B",
                "表格行: T1 | Parameter=Mode | Value=AB",
            ),
        )

    def test_digit_spacing_and_missing_unit_are_review_not_confirmed_change(self) -> None:
        """Extraction-only spacing/blank cells remain visible without becoming material."""

        spaced_old = (
            "表格行: T1 | Parameter=Length | Symbol=N | Host=4 0 | Module=4 0 | Units=UI"
        )
        spaced_new = (
            "表格行: T1 | Parameter=Length | Symbol=N | Host=40 | Module=40 | Units=UI"
        )
        missing_unit_old = (
            "表格行: T1 | Parameter=Delay | Symbol=t1 | Value=2.93x10-4 | Units="
        )
        missing_unit_new = (
            "表格行: T1 | Parameter=Delay | Symbol=t1 | Value=2.93×10-4 | Units=ns/mm"
        )

        self.assertEqual(
            "需人工复核",
            _table_structured_diff_kind(spaced_old, spaced_new),
        )
        self.assertEqual(
            "需人工复核",
            _table_structured_diff_kind(missing_unit_old, missing_unit_new),
        )
        self.assertNotEqual(
            "需人工复核",
            _table_structured_diff_kind(
                spaced_old.replace("Host=4 0", "Host=True 1"),
                spaced_new,
            ),
        )

    def test_geometry_proven_repeated_x_lanes_restore_one_merged_column(self) -> None:
        """Repeated categorical/numeric lanes recover a detector-missed boundary."""

        def word(text: str, x0: float, x1: float, top: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": top + 9.0,
                "size": 9.0,
            }

        rows: list[list[object]] = [
            ["Mode", "Host Channel Type\nInsertion Loss", "Zp"],
            ["Short", "near-end 0", "0"],
            ["Short", "far-end 12.0", "240"],
            ["Long", "near-end 8.0", "160"],
        ]
        word_rows: list[list[list[dict[str, object]]]] = [
            [
                [],
                [
                    word("Host", 205.0, 225.0, 100.0),
                    word("Channel", 228.0, 260.0, 100.0),
                    word("Type", 263.0, 280.0, 100.0),
                    word("Insertion", 325.0, 365.0, 100.0),
                    word("Loss", 368.0, 390.0, 100.0),
                ],
                [],
            ],
            [[], [word("near-end", 225.0, 261.0, 120.0), word("0", 365.0, 370.0, 120.0)], []],
            [[], [word("far-end", 229.0, 258.0, 140.0), word("12.0", 359.0, 376.0, 140.0)], []],
            [[], [word("near-end", 225.0, 261.0, 160.0), word("8.0", 361.0, 374.0, 160.0)], []],
        ]

        repaired_rows, repaired_words = (
            pdf_extract_module._split_geometry_proven_merged_table_column(
                rows,
                word_rows,
            )
        )

        self.assertEqual(4, len(repaired_rows[0]))
        self.assertEqual(
            ["Mode", "Host Channel Type", "Insertion Loss", "Zp"],
            repaired_rows[0],
        )
        self.assertEqual(["Short", "near-end", "0", "0"], repaired_rows[1])
        self.assertIsNotNone(repaired_words)
        unchanged_rows, _unchanged_words = (
            pdf_extract_module._split_geometry_proven_merged_table_column(
                rows[:3],
                word_rows[:3],
            )
        )
        self.assertEqual(rows[:3], unchanged_rows)
        self.assertNotEqual(
            "无变化",
            _table_structured_diff_kind(
                "表格行: T1 | Parameter=Mode | Symbol=A b | Value=1",
                "表格行: T1 | Parameter=Mode | Symbol=Ab | Value=1",
            ),
        )

    def test_geometry_proven_last_column_matches_an_already_split_table(self) -> None:
        """A detector-merged final column must not create a false table fact."""

        def word(text: str, x0: float, x1: float, top: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": x0,
                "x1": x1,
                "top": top,
                "bottom": top + 9.0,
                "size": 9.0,
            }

        merged_rows: list[list[object]] = [
            ["Mode", "Zp", "Channel Type\nInsertion Loss"],
            ["Short", "0", "near-end 0"],
            ["Short", "240", "far-end 12.0"],
            ["Long", "160", "near-end 8.0"],
        ]
        merged_word_rows: list[list[list[dict[str, object]]]] = [
            [
                [],
                [],
                [
                    word("Channel", 305.0, 337.0, 100.0),
                    word("Type", 340.0, 357.0, 100.0),
                    word("Insertion", 405.0, 445.0, 100.0),
                    word("Loss", 448.0, 470.0, 100.0),
                ],
            ],
            [
                [],
                [],
                [word("near-end", 325.0, 361.0, 120.0), word("0", 445.0, 450.0, 120.0)],
            ],
            [
                [],
                [],
                [word("far-end", 329.0, 358.0, 140.0), word("12.0", 439.0, 456.0, 140.0)],
            ],
            [
                [],
                [],
                [word("near-end", 325.0, 361.0, 160.0), word("8.0", 441.0, 454.0, 160.0)],
            ],
        ]
        split_rows: list[list[object]] = [
            ["Mode", "Zp", "Channel Type", "Insertion Loss"],
            ["Short", "0", "near-end", "0"],
            ["Short", "240", "far-end", "12.0"],
            ["Long", "160", "near-end", "8.0"],
        ]

        repaired_rows, repaired_words = (
            pdf_extract_module._split_geometry_proven_merged_table_column(
                merged_rows,
                merged_word_rows,
            )
        )
        repaired_lines = pdf_extract_module._table_lines_from_rows(
            repaired_rows,
            1,
            cell_word_rows=repaired_words,
        )
        split_lines = pdf_extract_module._table_lines_from_rows(split_rows, 1)

        self.assertEqual(split_rows, repaired_rows)
        self.assertEqual(split_lines, repaired_lines)

    def test_pua_and_unicode_without_font_provenance_remain_reviewable(self) -> None:
        """A lookalike code point cannot be declared equal without font evidence."""

        old_row = "表格行: T1 | Parameter=Noise | Symbol=\uf0670 | Value=1"
        new_row = "表格行: T1 | Parameter=Noise | Symbol=γ0 | Value=1"

        self.assertEqual("需人工复核", _table_structured_diff_kind(old_row, new_row))
        self.assertNotEqual(_review_unit_key("\uf0670"), _review_unit_key("γ0"))
        row_changes = _ordered_table_row_changes([old_row], [new_row])
        self.assertEqual(1, len(row_changes))
        self.assertEqual("需人工复核", row_changes[0].change_type)

        old_symbol_only = old_row.replace("Parameter=Noise | ", "")
        new_symbol_only = new_row.replace("Parameter=Noise | ", "")
        old_table = TableVisual(
            1,
            1,
            "Table 1. Noise",
            (0.0, 0.0, 100.0, 100.0),
            "",
            [old_symbol_only],
            "grid",
        )
        new_table = TableVisual(
            1,
            1,
            "Table 1. Noise",
            (0.0, 0.0, 100.0, 100.0),
            "",
            [new_symbol_only],
            "grid",
        )
        symbol_only_changes = _table_row_changes((old_table,), (new_table,))
        self.assertEqual(1, len(symbol_only_changes))
        self.assertEqual("需人工复核", symbol_only_changes[0].change_type)
        review_html = _render_table_row_change(symbol_only_changes[0])
        self.assertIn("未验证字体编码", review_html)
        self.assertIn("U+F067", review_html)
        self.assertIn("U+03B3", review_html)

        old_parameter_table = TableVisual(
            **{
                **old_table.__dict__,
                "row_texts": [
                    "表格行: T1 | Parameter=\uf067 limit | Value=1 | Units=dB"
                ],
            }
        )
        new_parameter_table = TableVisual(
            **{
                **new_table.__dict__,
                "row_texts": [
                    "表格行: T1 | Parameter=γ limit | Value=1 | Units=dB"
                ],
            }
        )
        parameter_changes = _table_row_changes(
            (old_parameter_table,),
            (new_parameter_table,),
        )
        self.assertEqual(1, len(parameter_changes))
        self.assertEqual("需人工复核", parameter_changes[0].change_type)
        self.assertIn(
            "U+F067",
            _render_table_row_change(parameter_changes[0]),
        )

        spaced_old_table = TableVisual(
            **{
                **old_table.__dict__,
                "row_texts": [old_symbol_only.replace("\uf0670", "\uf067 0")],
            }
        )
        spaced_changes = _table_row_changes((spaced_old_table,), (new_table,))
        self.assertEqual(1, len(spaced_changes))
        self.assertEqual("需人工复核", spaced_changes[0].change_type)
        spaced_html = _render_table_row_change(spaced_changes[0])
        self.assertIn("U+F067", spaced_html)
        self.assertIn("U+03B3", spaced_html)

        operator_change = _make_table_row_change(
            "表格行: T1 | Parameter=Range | Value=1\uf03c 2 | Units=dB",
            "表格行: T1 | Parameter=Range | Value=1<2 | Units=dB",
            "需人工复核",
        )
        operator_html = _render_table_row_change(operator_change)
        self.assertIn("U+F03C", operator_html)
        self.assertIn("U+003C", operator_html)
        self.assertEqual(
            "实质/符号变化",
            _table_structured_diff_kind(
                old_row,
                new_row.replace("Value=1", "Value=2"),
            ),
        )

    def test_pua_identity_collision_is_reviewed_only_when_full_value_multiset_matches(self) -> None:
        """Ambiguous mapped identities cannot create confirmed value changes."""

        def table(rows: list[str], *, title: str = "Table 1. Noise limits") -> TableVisual:
            return TableVisual(
                1,
                1,
                title,
                (0.0, 0.0, 100.0, 100.0),
                "",
                rows,
                "grid",
                row_alignment_reliable=True,
            )

        old_rows = [
            "表格行: T1 | Parameter=\uf067 limit | Value=1 | Units=dB",
            "表格行: T1 | Parameter=γ limit | Value=2 | Units=dB",
        ]
        swapped_rows = [
            "表格行: T1 | Parameter=\uf067 limit | Value=2 | Units=dB",
            "表格行: T1 | Parameter=γ limit | Value=1 | Units=dB",
        ]

        reviewed = _table_row_changes((table(old_rows),), (table(swapped_rows),))

        self.assertEqual(1, len(reviewed))
        self.assertTrue(all(row.change_type == "需人工复核" for row in reviewed))
        reviewed_html = "".join(_render_table_row_change(row) for row in reviewed)
        self.assertIn("U+F067", reviewed_html)
        self.assertIn("U+03B3", reviewed_html)

        materially_different = list(swapped_rows)
        materially_different[0] = materially_different[0].replace("Value=2", "Value=3")
        material = _table_row_changes(
            (table(old_rows),),
            (table(materially_different),),
        )
        self.assertTrue(any(row.change_type != "需人工复核" for row in material))
        self.assertTrue(any(row.change_type == "需人工复核" for row in material))

        mixed_old = [
            *old_rows,
            "表格行: T1 | Parameter=Other | Value=9 | Units=dB",
        ]
        mixed_new = [
            *swapped_rows,
            "表格行: T1 | Parameter=Other | Value=10 | Units=dB",
        ]
        mixed = _table_row_changes(
            (table(mixed_old),),
            (table(mixed_new),),
        )
        self.assertEqual(1, sum(row.change_type == "需人工复核" for row in mixed))
        self.assertEqual(1, sum(row.change_type != "需人工复核" for row in mixed))
        shared_body = "The receiver shall retain every declared noise limit. " * 8
        mixed_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-collision.pdf"),
                pages=[PageText(1, f"1 Scope\n{shared_body}")],
                table_visuals=[table(mixed_old)],
            ),
            ExtractionResult(
                pdf_path=Path("new-collision.pdf"),
                pages=[PageText(1, f"1 Scope\n{shared_body}")],
                table_visuals=[table(mixed_new)],
            ),
            DiffOptions(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            mixed_paths = write_reports(mixed_result, temp_dir, DiffOptions())
            mixed_audit = json.loads(
                mixed_paths["json"].read_text(encoding="utf-8")
            )
            mixed_html = mixed_paths["html"].read_text(encoding="utf-8")
        mixed_payload = mixed_audit["table_changes"][0]
        self.assertEqual("modified", mixed_payload["change_type"])
        self.assertEqual(1, mixed_payload["row_change_count"])
        self.assertEqual(1, mixed_payload["review_count"])
        self.assertIn("<strong>1</strong><span>变化表格</span>", mixed_html)
        self.assertIn("<strong>1</strong><span>表格复核项</span>", mixed_html)

        conditional_old = [
            *old_rows,
            "表格行: T1 | Parameter=Other | Value=9 | Units=dB | Condition=A",
        ]
        conditional_new = [
            *swapped_rows,
            "表格行: T1 | Parameter=Other | Value=9 | Units=dB | Condition=B",
        ]
        conditional = _table_row_changes(
            (table(conditional_old),),
            (table(conditional_new),),
        )
        self.assertEqual(
            1,
            sum(row.change_type == "需人工复核" for row in conditional),
        )
        self.assertEqual(
            1,
            sum(row.change_type != "需人工复核" for row in conditional),
        )

        multi_old: list[str] = []
        multi_new: list[str] = []
        for pua, literal, start in (("\uf067", "γ", 1), ("\uf061", "α", 3), ("\uf073", "σ", 5)):
            multi_old.extend(
                [
                    f"表格行: T1 | Parameter={pua} limit | Value={start} | Units=dB",
                    f"表格行: T1 | Parameter={literal} limit | Value={start + 1} | Units=dB",
                ]
            )
            multi_new.extend(
                [
                    f"表格行: T1 | Parameter={pua} limit | Value={start + 1} | Units=dB",
                    f"表格行: T1 | Parameter={literal} limit | Value={start} | Units=dB",
                ]
            )
        multi_reviews = _table_row_changes(
            (table(multi_old),),
            (table(multi_new),),
        )
        self.assertEqual(3, len(multi_reviews))
        self.assertEqual(
            ["U+F067", "U+F061", "U+F073"],
            [re.search(r"U\+F[0-9A-F]{3}", row.item).group(0) for row in multi_reviews],
        )

        fewer = _table_row_changes((table(old_rows),), (table(swapped_rows[:1]),))
        self.assertTrue(any(row.change_type != "需人工复核" for row in fewer))

        ordered = _table_row_changes(
            (table(old_rows, title="Table 1. Priority rules"),),
            (table(swapped_rows, title="Table 1. Priority rules"),),
        )
        self.assertTrue(any(row.change_type != "需人工复核" for row in ordered))

        ordinary_old = [
            "表格行: T1 | Parameter=Mode | Value=1 | Units=dB",
            "表格行: T1 | Parameter=Mode | Value=2 | Units=dB",
        ]
        ordinary_new = list(reversed(ordinary_old))
        ordinary = _table_row_changes(
            (table(ordinary_old),),
            (table(ordinary_new),),
        )
        self.assertTrue(any(row.change_type != "需人工复核" for row in ordinary))

        unknown_old = [row.replace("\uf067", "\ue123") for row in old_rows]
        unknown_new = [row.replace("\uf067", "\ue123") for row in swapped_rows]
        unknown = _table_row_changes(
            (table(unknown_old),),
            (table(unknown_new),),
        )
        self.assertTrue(any(row.change_type != "需人工复核" for row in unknown))
        self.assertEqual(
            _review_unit_key("Transmission line impedance z c 87.5 Ω."),
            _review_unit_key("Transmission line impedance zc 87.5 Ω."),
        )
        self.assertEqual(
            _review_unit_key("Transmission line impedance Z c is 87.5 ohm."),
            _review_unit_key("Transmission line impedance Zc is 87.5 ohm."),
        )
        for spaced, compact in (
            (
                "The characteristic impedance Z c shall be 87.5 ohm.",
                "The characteristic impedance Zc shall be 87.5 ohm.",
            ),
            (
                "The characteristic impedance Z c shall meet the requirement.",
                "The characteristic impedance Zc shall meet the requirement.",
            ),
            (
                "The impedance Z c is specified in Table 1.",
                "The impedance Zc is specified in Table 1.",
            ),
            (
                "The voltage A v equals the measured gain.",
                "The voltage Av equals the measured gain.",
            ),
            (
                "The impedance Z c, measured at TP1, is 87.5 ohm.",
                "The impedance Zc, measured at TP1, is 87.5 ohm.",
            ),
            (
                "The impedance Z c (see Figure 1) is 87.5 ohm.",
                "The impedance Zc (see Figure 1) is 87.5 ohm.",
            ),
        ):
            with self.subTest(spaced=spaced):
                self.assertEqual(_review_unit_key(spaced), _review_unit_key(compact))
        self.assertNotEqual(
            _review_unit_key("Select symbol A b before calibration."),
            _review_unit_key("Select symbol Ab before calibration."),
        )
        self.assertNotEqual(
            _review_unit_key("The expression is z c + 1."),
            _review_unit_key("The expression is zc + 1."),
        )
        for old_text, new_text in (
            (
                "The expression is z c + 1, and voltage is measured later.",
                "The expression is zc + 1, and voltage is measured later.",
            ),
            (
                "Let Z c denote two variables while resistance R is fixed.",
                "Let Zc denote two variables while resistance R is fixed.",
            ),
            (
                "For voltage analysis, the expression is A v + 1.",
                "For voltage analysis, the expression is Av + 1.",
            ),
            (
                "Let resistance R be fixed and Z c denote two variables.",
                "Let resistance R be fixed and Zc denote two variables.",
            ),
            (
                "For voltage analysis the expression is A v + 1.",
                "For voltage analysis the expression is Av + 1.",
            ),
            (
                "The voltage list contains A v entries.",
                "The voltage list contains Av entries.",
            ),
            (
                "The impedance list contains Z c entries.",
                "The impedance list contains Zc entries.",
            ),
            (
                "The receiver voltage uses A v as two separate labels.",
                "The receiver voltage uses Av as two separate labels.",
            ),
            (
                "The current note says A v is not one symbol.",
                "The current note says Av is not one symbol.",
            ),
            (
                "The voltage A v denotes two separate labels.",
                "The voltage Av denotes two separate labels.",
            ),
            (
                "The impedance Z c is not one symbol.",
                "The impedance Zc is not one symbol.",
            ),
            (
                "The voltage A v and B are separate variables.",
                "The voltage Av and B are separate variables.",
            ),
            (
                "The impedance Z c should remain spaced.",
                "The impedance Zc should remain spaced.",
            ),
            (
                "The voltage A v means A multiplied by v.",
                "The voltage Av means A multiplied by v.",
            ),
            (
                "The impedance Z c represents Z times c.",
                "The impedance Zc represents Z times c.",
            ),
        ):
            with self.subTest(old_text=old_text):
                self.assertNotEqual(_review_unit_key(old_text), _review_unit_key(new_text))

        for spaced, compact in (
            (
                "The impedance Z c is 87.5 ohm, while separate labels identify test modes.",
                "The impedance Zc is 87.5 ohm, while separate labels identify test modes.",
            ),
            (
                "The voltage A v is 1 V and the table uses separate symbols.",
                "The voltage Av is 1 V and the table uses separate symbols.",
            ),
            (
                "The impedance Z c is specified; separate variables are defined below.",
                "The impedance Zc is specified; separate variables are defined below.",
            ),
            (
                "The impedance Z c is 87.5 ohm because separate labels identify lanes.",
                "The impedance Zc is 87.5 ohm because separate labels identify lanes.",
            ),
        ):
            with self.subTest(spaced=spaced):
                self.assertEqual(_review_unit_key(spaced), _review_unit_key(compact))

    def test_body_pua_and_unicode_without_font_provenance_are_not_silently_equal(self) -> None:
        """The end-to-end body comparator keeps an unproven glyph change visible."""

        old = ExtractionResult(
            pdf_path=Path("old-custom-font.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nThe receiver shall preserve custom glyph \uf067 in this mode.",
                )
            ],
        )
        new = ExtractionResult(
            pdf_path=Path("new-unicode.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text="1 Scope\nThe receiver shall preserve custom glyph γ in this mode.",
                )
            ],
        )

        result = compare_extractions(old, new, DiffOptions())

        self.assertTrue(result.changes)
        self.assertEqual("review", result.changes[0].change_type)
        self.assertTrue(
            any(
                change.replaced_snippets
                or change.removed_snippets
                or change.added_snippets
                for change in result.changes
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            reader_outputs = [
                paths[format_name].read_text(encoding="utf-8")
                for format_name in ("html", "markdown", "text")
            ]
        for reader_output in reader_outputs:
            self.assertIn("未验证字体编码", reader_output)
            self.assertIn("U+F067", reader_output)
            self.assertIn("U+03B3", reader_output)
            self.assertIn("需人工复核", reader_output)
        self.assertIn(
            "<strong>0</strong><span>核心技术变化</span>",
            reader_outputs[0],
        )
        self.assertIn(
            "<strong>1</strong><span>正文字符复核项</span>",
            reader_outputs[0],
        )

        operator_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-symbol-operator.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Scope\nThe receiver shall verify 1\uf03c 2.",
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-unicode-operator.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Scope\nThe receiver shall verify 1 < 2.",
                    )
                ],
            ),
            DiffOptions(),
        )
        self.assertEqual("review", operator_result.changes[0].change_type)
        with tempfile.TemporaryDirectory() as temp_dir:
            operator_html = write_reports(
                operator_result,
                temp_dir,
                DiffOptions(),
            )["html"].read_text(encoding="utf-8")
        self.assertIn("<strong>0</strong><span>核心技术变化</span>", operator_html)
        self.assertIn("<strong>1</strong><span>正文字符复核项</span>", operator_html)
        self.assertIn("U+F03C", operator_html)

        operator_wording_result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-symbol-operator-wording.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Scope\nThe receiver shall verify 1\uf03c 2 in mode A.",
                    )
                ],
            ),
            ExtractionResult(
                pdf_path=Path("new-unicode-operator-wording.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Scope\nThe receiver shall verify 1 < 2 in mode B.",
                    )
                ],
            ),
            DiffOptions(),
        )
        self.assertEqual("modified", operator_wording_result.changes[0].change_type)

        mixed_result = compare_extractions(
            old,
            ExtractionResult(
                pdf_path=Path("new-unicode-and-wording.pdf"),
                pages=[
                    PageText(
                        page_number=1,
                        text=(
                            "1 Scope\nThe receiver shall preserve custom glyph γ "
                            "in the revised mode."
                        ),
                    )
                ],
            ),
            DiffOptions(),
        )
        self.assertTrue(mixed_result.changes)
        self.assertEqual("modified", mixed_result.changes[0].change_type)

    def test_reader_inline_highlight_does_not_mark_normalized_symbols(self) -> None:
        """Only the changed value is colored when symbol spelling is reader-equivalent."""

        cases = (
            ("\uf0670", "γ0"),
            ("Z c", "Zc"),
        )
        for old_symbol, new_symbol in cases:
            with self.subTest(symbols=(old_symbol, new_symbol)):
                change = _make_table_row_change(
                    f"表格行: T1 | Parameter=Limit | Symbol={old_symbol} | Value=1",
                    f"表格行: T1 | Parameter=Limit | Symbol={new_symbol} | Value=2",
                    "实质变化",
                )
                html = _render_table_row_change(change)

                self.assertNotRegex(html, r'<mark class="(?:del|ins)">(?:γ0|Zc)</mark>')
                self.assertIn('<mark class="del">1</mark>', html)
                self.assertIn('<mark class="ins">2</mark>', html)

    def test_table_row_projection_never_hides_changed_fields_or_schema(self) -> None:
        """Every changed structured field remains visible in item/old/new facts."""

        cases = (
            (
                "表格行: T1 | Parameter=Voltage | Symbol=Vout | Units=V",
                "表格行: T1 | Parameter=Voltage | Symbol=Vout | Units=mV",
                ("Units=V", "Units=mV"),
            ),
            (
                "表格行: T1 | Parameter=P | Description=Old definition | Value=1 | Units=V",
                "表格行: T1 | Parameter=P | Description=New definition | Value=1 | Units=V",
                ("Description=Old definition", "Description=New definition"),
            ),
            (
                "表格行: T1 | Parameter=P | Min=1 | Units=V",
                "表格行: T1 | Parameter=P | Minimum=1 | Units=V",
                ("Min=1", "Minimum=1"),
            ),
            (
                "表格行: T1 | Parameter=P | Value=1 | Units=V",
                "表格行: T1 | Parameter=P | Max=1 | Units=V",
                ("Value=1", "Max=1"),
            ),
            (
                "表格行: T1 | Parameter=P | Symbol=X | Unit=V | Units=mV",
                "表格行: T1 | Parameter=P | Symbol=X | Unit=V | Units=uV",
                ("Units=mV", "Units=uV"),
            ),
            (
                "表格行: T1 | Parameter=P | raw-old | Value=1 | Units=V",
                "表格行: T1 | Parameter=P | raw-new | Value=1 | Units=V",
                ("列2=raw-old", "列2=raw-new"),
            ),
        )
        for old_row, new_row, expected in cases:
            with self.subTest(expected=expected):
                change = _make_table_row_change(old_row, new_row, "替换/修改")
                visible = f"{change.item}\n{change.old_value}\n{change.new_value}"

                self.assertIn(expected[0], visible)
                self.assertIn(expected[1], visible)
                self.assertNotEqual(change.old_value, change.new_value)

    def test_reader_inline_highlight_preserves_spacing_changes_outside_symbol_fields(self) -> None:
        """A Value-space change remains readable and highlighted in the report."""

        change = _make_table_row_change(
            "表格行: T1 | Parameter=Mode | Symbol=X | Value=A v",
            "表格行: T1 | Parameter=Mode | Symbol=X | Value=Av",
            "替换/修改",
        )
        html = _render_table_row_change(change)

        self.assertIn("Symbol=X", html)
        self.assertIn('<mark class="del">', html)
        self.assertIn('<mark class="ins">', html)
        self.assertIn(" ", html)
        self.assertNotEqual(change.old_value, change.new_value)

    def test_symbol_only_rows_pair_by_normalized_symbol_identity(self) -> None:
        """Reader-equivalent Symbol spelling must produce one modified row."""

        old_table = TableVisual(
            1,
            1,
            "Table 1. Symbol limits",
            (0.0, 0.0, 100.0, 100.0),
            "",
            ["表格行: T1 | Symbol=Z c | Value=1 | Units=dB"],
            "grid",
        )
        new_table = TableVisual(
            1,
            1,
            "Table 1. Symbol limits",
            (0.0, 0.0, 100.0, 100.0),
            "",
            ["表格行: T1 | Symbol=Zc | Value=2 | Units=dB"],
            "grid",
        )

        changes = _table_row_changes((old_table,), (new_table,))

        self.assertEqual(1, len(changes))
        self.assertEqual("实质变化", changes[0].change_type)
        self.assertIn("Symbol=Zc", changes[0].new_value)

    def test_known_pua_is_readable_in_all_reader_reports_but_raw_json_is_unchanged(self) -> None:
        """Reader HTML/Markdown/TXT decode known glyphs without rewriting audit facts."""

        old = ExtractionResult(
            pdf_path=Path("old-symbol.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\nNote: ADD and \uf073 calculated from RJ measurements "
                        "use the range f\uf0a42 with a 50\uf057 load in Table XXX."
                    ),
                )
            ],
        )
        new = ExtractionResult(
            pdf_path=Path("new-symbol.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "1 Scope\nNote: ADD and \uf073 calculated from RJ measurements "
                        "use the range f\uf0a42 with a 50\uf057 load in Table 31-14."
                    ),
                )
            ],
        )
        result = compare_extractions(old, new, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            reader_outputs = [
                paths[format_name].read_text(encoding="utf-8")
                for format_name in ("html", "markdown", "text")
            ]
            audit = json.loads(paths["json"].read_text(encoding="utf-8"))

        for reader_output in reader_outputs:
            self.assertIn("σ", reader_output)
            self.assertIn("f⁄2", reader_output)
            self.assertIn("50Ω", reader_output)
            self.assertNotIn("\uf073", reader_output)
            self.assertNotIn("\uf0a4", reader_output)
            self.assertNotIn("\uf057", reader_output)
        audit_text = json.dumps(audit, ensure_ascii=False)
        self.assertIn("\uf073", audit_text)
        self.assertIn("\uf0a4", audit_text)
        self.assertIn("\uf057", audit_text)

    def test_table_row_pua_is_raw_in_json_csv_and_readable_in_reader_formats(self) -> None:
        """Table facts keep source glyphs while HTML/Markdown/TXT decode the whitelist."""

        old_row = (
            "表格行: T1 | Parameter=Noise limit | Symbol=\uf0670 | "
            "Value=1\uf03c2 | Units=dB"
        )
        new_row = old_row.replace("1\uf03c2", "1\uf03c3")
        old_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1. Noise limits",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[old_row],
            grid_summary="grid",
        )
        new_table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1. Noise limits",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[new_row],
            grid_summary="grid",
        )
        shared = "The receiver shall preserve every declared noise requirement. " * 8
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-table-pua.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{shared}")],
                table_visuals=[old_table],
            ),
            ExtractionResult(
                pdf_path=Path("new-table-pua.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{shared}")],
                table_visuals=[new_table],
            ),
            DiffOptions(),
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, DiffOptions())
            reader_outputs = {
                kind: paths[kind].read_text(encoding="utf-8")
                for kind in ("html", "markdown", "text")
            }
            audit = json.loads(paths["json"].read_text(encoding="utf-8"))
            csv_text = paths["table_csv"].read_text(encoding="utf-8")

        for kind, reader_output in reader_outputs.items():
            self.assertIn("γ0", reader_output)
            self.assertIn("&lt;" if kind == "html" else "<", reader_output)
            self.assertIsNone(re.search(r"[\ue000-\uf8ff]", reader_output))
        row_fact = audit["table_changes"][0]["row_changes"][0]
        self.assertIn("\uf0670", row_fact["old_value"])
        self.assertIn("\uf03c", row_fact["old_value"])
        self.assertIn("\uf0670", csv_text)
        self.assertIn("\uf03c", csv_text)

    def test_consecutive_short_technical_labels_remain_visible(self) -> None:
        """Short labels need structured provenance before any reader folding."""

        html = _render_single_list(
            "删除片段",
            ["MCB", "Reference", "HCB", "Table 31-13.", "TP4a"],
            "del",
        )

        self.assertEqual(5, html.count("<li>"))
        self.assertNotIn("图示中的短标签已合并折叠", html)
        for label in ("MCB", "Reference", "HCB", "Table 31-13.", "TP4a"):
            self.assertIn(label, html)
        standalone_reference = _render_single_list(
            "删除片段",
            ["Table 31-13."],
            "del",
        )
        self.assertNotIn("图示中的短标签已合并折叠", standalone_reference)
        self.assertIsNone(_reader_snippet_collapse_kind("Stressed signal"))
        self.assertIsNone(
            _reader_snippet_collapse_kind("Termination and TP1a crosstalk calibration")
        )
        self.assertEqual("layout", _reader_snippet_collapse_kind("f"))
        self.assertIsNone(
            _reader_snippet_collapse_kind(
                "The receiver support is removed from this operating mode."
            )
        )

    def test_short_orphan_formula_identifiers_are_folded_but_real_prose_is_not(self) -> None:
        """Short FFE/subscript walls stay auditable without reading like prose."""

        for fragment in (
            "Bd_12DDS f f f",
            "fx bx FFE_Post",
            "FFE_post bg bf",
        ):
            with self.subTest(fragment=fragment):
                self.assertEqual("layout", _reader_snippet_collapse_kind(fragment))
                rendered = _render_single_list("删除片段", [fragment], "del")
                self.assertIn("疑似表格或公式的版面文字已折叠", rendered)
                self.assertNotIn(fragment, rendered)
                self.assertIn("protocol_diff_data.json", rendered)
        self.assertIsNone(
            _reader_snippet_collapse_kind(
                "FFE_post changes the receiver response for this operating mode."
            )
        )

    def test_folded_single_side_table_wall_exposes_critical_limit_anchor(self) -> None:
        """A long deletion keeps a compact limit anchor even when its body is folded."""

        fragment = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level "
            "Label Description Reference First Last "
            + " ".join(f"P{index} X{index} {index} UI" for index in range(30))
            + " CRITICAL_LIMIT = 3 dB"
        )

        self.assertEqual("layout", _reader_snippet_collapse_kind(fragment))
        rendered = _render_single_list("删除片段", [fragment], "del")
        self.assertIn("疑似表格或公式的版面文字已折叠", rendered)
        self.assertIn("关键锚点", rendered)
        self.assertIn("CRITICAL_LIMIT = 3 dB", rendered)

    def test_normative_sentence_with_known_symbol_glyphs_is_not_folded_as_layout(self) -> None:
        """Known Symbol-font glyphs and many limits do not erase a requirement."""

        sentence = (
            "The transmitter shall use \uf067, \uf073, \uf074, and \uf061 when limits 1 2 3 4 "
            "5 6 7 8 9 10 11 12 are evaluated for every supported operating mode, "
            "and the receiver shall preserve the resulting values."
        )

        self.assertIsNone(_reader_snippet_collapse_kind(sentence))
        rendered = _render_single_list("删除片段", [sentence], "del")
        self.assertNotIn("疑似表格或公式的版面文字已折叠", rendered)
        self.assertIn("The transmitter shall use", rendered)
        self.assertIn("γ", rendered)

    def test_short_unknown_pua_formula_is_folded_without_hiding_readable_tail(self) -> None:
        """A short formula with ``where/is`` must not bypass the gibberish guard."""

        fragment = "\ue111 \ue112 \ue113 \ue114 P1 = 1 + 2 / 3, where x is 4."

        self.assertEqual("layout", _reader_snippet_collapse_kind(fragment))
        rendered = _render_single_list("删除片段", [fragment], "del")
        self.assertIn("疑似表格或公式的版面文字已折叠", rendered)
        self.assertIn("可读正文", rendered)
        self.assertIn("where x is 4.", rendered)
        for glyph in "\ue111\ue112\ue113\ue114":
            self.assertNotIn(glyph, rendered)

        normative = (
            "The receiver shall preserve unknown glyph \ue111 while every declared "
            "operating requirement remains traceable."
        )
        self.assertIsNone(_reader_snippet_collapse_kind(normative))
        normative_html = _render_single_list("删除片段", [normative], "del")
        self.assertIn("The receiver shall preserve", normative_html)
        self.assertIn("未识别符号 U+E111", normative_html)
        self.assertNotIn("\ue111", normative_html)

    def test_s_parameter_formula_wall_keeps_where_and_editor_note_readable(self) -> None:
        """Formula tokens fold while their explanatory prose remains visible."""

        fragment = (
            "Mated HCB-MCB SCD11 SCD22 1 2 3 4 5 6 7 8 9 10 11 12 "
            "SCD11  -22 + 4 f dB SCD22  -25 + 5 f dB Equation 31-7 "
            "for 0.05 GHz  f  28 GHz SCD11 SCD22  -18 + 3 f dB "
            "for 28 GHz  f  50 GHz Where port 2 is TP1a and TP4, "
            "port 1 is TP1 and TP4a Editor’s note: Above equations are to be updated."
        )

        self.assertEqual("layout", _reader_snippet_collapse_kind(fragment))
        tail = _reader_visible_prose_tail(fragment)
        self.assertTrue(tail.startswith("Where port 2 is TP1a"))
        self.assertIn("Editor’s note: Above equations are to be updated.", tail)

    def test_coordinate_proven_table_body_duplicate_is_reader_only(self) -> None:
        """Lossless table text may leave reader cards, but remains in the audit model."""

        old_snippet = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level Label "
            "Description Reference First Last "
            + " ".join(str(value) for value in range(1, 101))
        )
        new_snippet = (
            "Value Units Parameter Symbol Conditions Index Transition Threshold Level Label "
            "Description Reference First Last "
            + " ".join(str(value) for value in range(1, 101))
        )
        old_section = Section(
            "old-table",
            "9 Coordinate Duplicate Section",
            "Coordinate Duplicate Section",
            1,
            ("9 Coordinate Duplicate Section",),
            ("9",),
            10,
            10,
            old_snippet,
        )
        new_section = Section(
            "new-table",
            "9 Coordinate Duplicate Section",
            "Coordinate Duplicate Section",
            1,
            ("9 Coordinate Duplicate Section",),
            ("9",),
            11,
            11,
            new_snippet,
        )
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            replaced_snippets=[SnippetPair(old_snippet, new_snippet)],
        )
        old_table = TableVisual(
            page_number=10,
            table_number=1,
            title="Table 9-1. Coordinate-backed values",
            bbox=(10.0, 20.0, 500.0, 700.0),
            image_data_uri="",
            row_texts=[old_snippet],
            grid_summary="structured rows",
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        new_table = TableVisual(
            page_number=11,
            table_number=1,
            title="Table 9-1. Coordinate-backed values",
            bbox=(10.0, 20.0, 500.0, 700.0),
            image_data_uri="",
            row_texts=[new_snippet],
            grid_summary="structured rows",
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        table_change = TableChange(
            "modified",
            (old_table,),
            (new_table,),
            0.99,
            False,
            (TableRowChange("Value order", "old", "new", "替换/修改"),),
        )

        self.assertTrue(
            _reader_change_is_coordinate_proven_table_body_duplicate(
                change,
                [table_change],
            )
        )
        unproven_old_table = TableVisual(
            **{
                **old_table.__dict__,
                "content_fully_represented": False,
            }
        )
        unproven_table_change = TableChange(
            **{
                **table_change.__dict__,
                "old_tables": (unproven_old_table,),
            }
        )
        self.assertFalse(
            _reader_change_is_coordinate_proven_table_body_duplicate(
                change,
                [unproven_table_change],
            )
        )
        self.assertFalse(
            _reader_change_is_coordinate_proven_table_body_duplicate(
                change,
                [
                    TableChange(
                        **{
                            **table_change.__dict__,
                            "new_tables": (),
                        }
                    )
                ],
            )
        )

        unreliable_old = TableVisual(
            **{
                **old_table.__dict__,
                "row_alignment_reliable": False,
            }
        )
        unreliable_new = TableVisual(
            **{
                **new_table.__dict__,
                "row_alignment_reliable": False,
            }
        )
        explicit_review = TableChange(
            "review",
            (unreliable_old,),
            (unreliable_new,),
            0.99,
            False,
            (
                TableRowChange(
                    "表格行归属",
                    "多行单元格归属未验证",
                    "多行单元格归属未验证",
                    "需人工复核",
                ),
            ),
        )
        self.assertTrue(
            _reader_change_is_coordinate_proven_table_body_duplicate(
                change,
                [explicit_review],
            )
        )
        self.assertFalse(
            _reader_change_is_coordinate_proven_table_body_duplicate(
                change,
                [reporting_module._TableVisualGroup((unreliable_old,), (unreliable_new,))],
            )
        )

    def test_mixed_section_hides_only_snippets_covered_by_visible_table_evidence(self) -> None:
        """A mixed section keeps prose while screenshot-backed table walls leave no fold notice."""

        # 该长串模拟真实 058 中由多页表格线性化后形成的不可读文字墙。
        table_wall = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level "
            "Label Description Reference First Last "
            + " ".join(f"P{index} X{index} {index} UI" for index in range(1, 45))
        )
        # 这些短片段模拟同一表格被拆成多条新增记录，并包含两个只在限流后审计列表中的条目。
        table_fragments = [
            "Parameter Symbol Value Units",
            "Transmitter equalizer, 3rd pre-cursor coefficient c(-3)",
            "Minimum value 0 —",
            "Maximum value 0 —",
            "Step size 0.02 —",
        ]
        normal_prose = (
            "For channel compliance testing, the transmitter package claimed by the "
            "vendor should be used."
        )
        old_section = Section(
            "old-mixed-table",
            "9 Mixed table section",
            "Mixed table section",
            1,
            ("9 Mixed table section",),
            ("9",),
            10,
            10,
            table_wall,
        )
        new_section = replace(old_section, section_id="new-mixed-table", start_page=11, end_page=11)
        prose_pair = SnippetPair(
            "The package model is defined in IEEE 802.3dj Clause 178A.",
            "The package model is defined in IEEE Std 802.3dj Clause 178A.",
        )
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            added_snippets=[normal_prose, *table_fragments[:2]],
            removed_snippets=[table_wall],
            replaced_snippets=[prose_pair],
            omitted_snippet_count=3,
            audit_added_snippets=[normal_prose, *table_fragments],
            audit_removed_snippets=[table_wall],
            audit_replaced_snippets=[prose_pair],
        )
        # 两侧表格大体一致，只保留一个真实 R0 数值变化来生成可见表格复核卡。
        common_rows = [
            f"表格行: T1 | Parameter={table_wall} | Symbol= | Value= | Units=",
            (
                "表格行: T1 | Parameter=Transmitter equalizer, 3rd pre-cursor "
                "coefficient — Minimum value\nMaximum value\nStep size | Symbol=c(-3) | "
                "Value=0\n0\n0.02 | Units=—\n—\n—"
            ),
        ]
        old_table = TableVisual(
            10,
            1,
            "Table 9-1. COM Parameter Values",
            (10.0, 20.0, 500.0, 700.0),
            "data:image/jpeg;base64,AA==",
            [*common_rows, "表格行: T1 | Parameter=R0 | Symbol=R0 | Value=50 | Units=Ω"],
            "structured rows",
            content_fully_represented=True,
            row_alignment_reliable=False,
        )
        new_table = replace(
            old_table,
            page_number=11,
            row_texts=[
                *common_rows,
                "表格行: T1 | Parameter=R0 | Symbol=R0 | Value=46.25 | Units=Ω",
            ],
        )
        result = DiffResult(
            Path("old.pdf"),
            Path("new.pdf"),
            [old_section],
            [new_section],
            [change],
            [],
            old_total_pages=11,
            new_total_pages=11,
            old_table_visuals=[old_table],
            new_table_visuals=[new_table],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            # 通过公开报告入口同时验证 HTML、Markdown、TXT 和无损 JSON 审计面。
            paths = write_reports(result, temp_dir, DiffOptions())
            html = paths["html"].read_text(encoding="utf-8")
            markdown = paths["markdown"].read_text(encoding="utf-8")
            text = paths["text"].read_text(encoding="utf-8")
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))

        # 正常技术句和独立替换必须留在读者报告，表格截图继续提供原页证据。
        for reader_report in (html, markdown, text):
            self.assertIn(normal_prose, reader_report)
            self.assertIn("The package model is defined", reader_report)
            self.assertIn("802.3dj Clause 178A", reader_report)
            self.assertIn("Table 9-1. COM Parameter Values", reader_report)
            self.assertNotIn(table_wall, reader_report)
            self.assertNotIn(table_fragments[0], reader_report)
            self.assertNotIn("疑似表格或公式的版面文字已折叠", reader_report)
            self.assertNotIn("处未展示", reader_report)
        # JSON 必须继续保存被读者层隐藏的完整 occurrence 和原始省略计数。
        self.assertIn(table_wall, payload["changes"][0]["removed_snippets"])
        self.assertEqual(table_fragments, payload["changes"][0]["added_snippets"][1:])
        self.assertEqual(3, payload["changes"][0]["omitted_snippet_count"])

    def test_incomplete_table_visual_cannot_hide_one_mixed_section_fragment(self) -> None:
        """Without complete bbox coverage, even obvious table text stays visible for review."""

        table_wall = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level "
            + " ".join(str(index) for index in range(1, 90))
        )
        old_section = Section(
            "old-incomplete-table",
            "9 Incomplete table section",
            "Incomplete table section",
            1,
            ("9 Incomplete table section",),
            ("9",),
            10,
            10,
            table_wall,
        )
        new_section = replace(old_section, section_id="new-incomplete-table", start_page=11, end_page=11)
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            removed_snippets=[table_wall],
        )
        old_table = TableVisual(
            10,
            1,
            "Table 9-1. Incomplete values",
            (10.0, 20.0, 500.0, 700.0),
            "",
            [table_wall],
            "structured rows",
            content_fully_represented=False,
            row_alignment_reliable=True,
        )
        new_table = replace(old_table, page_number=11)
        evidence = TableChange("modified", (old_table,), (new_table,), 1.0, False, ())

        # 不完整截图只能作为辅助证据，不能授权读者层删除正文片段。
        cleaned = _reader_section_change(change, [evidence])
        self.assertIsNotNone(cleaned)
        self.assertEqual([table_wall], cleaned.removed_snippets)

    def test_table_wall_filter_preserves_readable_normative_suffixes(self) -> None:
        """A table-heavy snippet may be shortened, but its changed prose tail must survive."""

        table_body = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level Label "
            "Description Reference First Last "
            + " ".join(str(value) for value in range(1, 101))
        )
        old_tail = "The receiver shall remain disabled during link training."
        new_tail = "The receiver shall remain enabled during link training."
        old_section = Section(
            "old-table-tail",
            "9 Table with prose tail",
            "Table with prose tail",
            1,
            ("9 Table with prose tail",),
            ("9",),
            10,
            10,
            f"{table_body} {old_tail}",
        )
        new_section = replace(
            old_section,
            section_id="new-table-tail",
            start_page=11,
            end_page=11,
            body=f"{table_body} {new_tail}",
        )
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            removed_snippets=[f"{table_body} {old_tail}"],
            added_snippets=[f"{table_body} {new_tail}"],
            audit_removed_snippets=[f"{table_body} {old_tail}"],
            audit_added_snippets=[f"{table_body} {new_tail}"],
            audit_replaced_snippets=[],
        )
        old_table = TableVisual(
            10,
            1,
            "Table 9-1. Coordinate-backed values",
            (10.0, 20.0, 500.0, 700.0),
            "",
            [table_body],
            "structured rows",
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        new_table = replace(old_table, page_number=11)
        evidence = TableChange("modified", (old_table,), (new_table,), 1.0, False, ())

        cleaned = _reader_section_change(change, [evidence])

        self.assertIsNotNone(cleaned)
        self.assertEqual([old_tail], cleaned.removed_snippets)
        self.assertEqual([new_tail], cleaned.added_snippets)
        self.assertEqual([old_tail], cleaned.audit_removed_snippets)
        self.assertEqual([new_tail], cleaned.audit_added_snippets)

    def test_table_fragment_filter_consumes_duplicate_text_occurrences(self) -> None:
        """One visible table row cannot authorize deletion of two equal occurrences."""

        row = "Minimum value 0 —"
        cleaned = reporting_module._reader_filter_evidenced_table_fragments(
            [row, row],
            f"Parameter Symbol Value Units {row}",
        )

        self.assertEqual([row], cleaned)

    def test_public_reader_evidence_deduplicates_change_and_visual_group(self) -> None:
        """The same physical table in two evidence collections still counts once."""

        row = "Minimum value 0 —"
        old_section = Section(
            "old-duplicate-evidence",
            "9 Duplicate evidence",
            "Duplicate evidence",
            1,
            ("9 Duplicate evidence",),
            ("9",),
            10,
            10,
            f"{row}\n{row}",
        )
        new_section = replace(
            old_section,
            section_id="new-duplicate-evidence",
            start_page=11,
            end_page=11,
        )
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            removed_snippets=[row, row],
        )
        old_table = TableVisual(
            10,
            1,
            "Table 9-1. One row",
            (10.0, 20.0, 500.0, 700.0),
            "",
            [row],
            "structured rows",
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        new_table = replace(old_table, page_number=11)
        table_change = TableChange(
            "modified",
            (old_table,),
            (new_table,),
            1.0,
            False,
            (),
        )
        visual_group = reporting_module._TableVisualGroup(
            (old_table,),
            (new_table,),
        )

        cleaned = _reader_section_change(change, [table_change, visual_group])

        self.assertIsNotNone(cleaned)
        self.assertEqual([row], cleaned.removed_snippets)

    def test_whole_card_duplicate_proof_counts_one_physical_table_once(self) -> None:
        """Duplicate evidence collections cannot combine one table into two-table proof."""

        table_body = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level Label "
            "Description Reference First Last "
            + " ".join(f"P{index} S{index} {index} UI" for index in range(1, 35))
        )
        duplicated_body = f"{table_body} {table_body}"
        old_section = Section(
            "old-whole-card-budget",
            "9 Whole card budget",
            "Whole card budget",
            1,
            ("9 Whole card budget",),
            ("9",),
            10,
            10,
            duplicated_body,
        )
        new_section = replace(
            old_section,
            section_id="new-whole-card-budget",
            start_page=11,
            end_page=11,
        )
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            replaced_snippets=[SnippetPair(duplicated_body, duplicated_body)],
        )
        old_table = TableVisual(
            10,
            1,
            "Table 9-1. Whole card values",
            (10.0, 20.0, 500.0, 700.0),
            "",
            [table_body],
            "structured rows",
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        new_table = replace(old_table, page_number=11)
        table_change = TableChange(
            "modified",
            (old_table,),
            (new_table,),
            1.0,
            False,
            (),
        )
        visual_group = reporting_module._TableVisualGroup(
            (old_table,),
            (new_table,),
        )

        single = _reader_change_is_coordinate_proven_table_body_duplicate(
            change,
            [table_change],
        )
        duplicated = _reader_change_is_coordinate_proven_table_body_duplicate(
            change,
            [table_change, visual_group],
        )

        self.assertFalse(single)
        self.assertEqual(single, duplicated)

    def test_empty_cell_dash_does_not_understate_structured_row_occurrences(self) -> None:
        """A display dash is not an occurrence identity when fields move in serialization."""

        snippet = "Step size 0.02 —"
        structured_rows = " ".join(
            "Parameter=Coefficient — Minimum value\\nMaximum value\\nStep size | "
            "Symbol=c(0) | Value=0\\n0\\n0.02 | Units=—\\n—\\n—"
            for _index in range(3)
        )

        cleaned = reporting_module._reader_filter_evidenced_table_fragments(
            [snippet, snippet, snippet],
            structured_rows,
        )

        self.assertEqual([], cleaned)

    def test_greek_symbol_remains_part_of_table_occurrence_identity(self) -> None:
        """Ignoring an empty-cell dash must not erase a Greek technical symbol."""

        snippet = "Step size α —"
        table_text = "Step size α — Step size β — Step size γ —"

        cleaned = reporting_module._reader_filter_evidenced_table_fragments(
            [snippet, snippet, snippet],
            table_text,
        )

        self.assertEqual([snippet, snippet], cleaned)

    def test_comparison_operator_remains_part_of_table_occurrence_identity(self) -> None:
        """Limits with ≤, ≥ and = are different physical row occurrences."""

        snippet = "Step size ≤ —"
        table_text = "Step size ≤ — Step size ≥ — Step size = —"

        cleaned = reporting_module._reader_filter_evidenced_table_fragments(
            [snippet, snippet, snippet],
            table_text,
        )

        self.assertEqual([snippet, snippet], cleaned)

    def test_tiny_formula_between_table_rows_is_not_treated_as_a_bridge(self) -> None:
        """A one-letter formula remains visible even when adjacent rows are table-backed."""

        snippets = ["Parameter Symbol Value Units", "f", "Minimum value 0 UI"]
        table_text = "Parameter Symbol Value Units f Minimum value 0 UI"

        cleaned = reporting_module._reader_filter_evidenced_table_fragments(
            snippets,
            table_text,
        )

        self.assertEqual(["f"], cleaned)

    def test_remote_table_page_cannot_hide_same_text_in_long_section(self) -> None:
        """Same text on a non-table page makes the occurrence provenance ambiguous."""

        row = "Minimum value 0 —"
        old_section = Section(
            "old-remote-row",
            "9 Long section",
            "Long section",
            1,
            ("9 Long section",),
            ("9",),
            1,
            20,
            row,
            page_bodies=((1, row), (20, f"Parameter Symbol Value Units {row}")),
        )
        new_section = replace(old_section, section_id="new-remote-row")
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            removed_snippets=[row],
        )
        old_table = TableVisual(
            20,
            1,
            "Table 9-1. Remote values",
            (10.0, 20.0, 500.0, 700.0),
            "",
            [f"Parameter Symbol Value Units {row}"],
            "structured rows",
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        evidence = TableChange(
            "modified",
            (old_table,),
            (old_table,),
            1.0,
            False,
            (),
        )

        cleaned = _reader_section_change(change, [evidence])

        self.assertIsNotNone(cleaned)
        self.assertEqual([row], cleaned.removed_snippets)

    def test_table_filter_refills_reader_capacity_across_delta_kinds(self) -> None:
        """Removed table noise yields its slots to normal replacements, not omitted text."""

        table_rows = ["Parameter Symbol Value Units", "Minimum value 0 —"]
        old_section = Section(
            "old-cross-kind-refill",
            "9 Mixed deltas",
            "Mixed deltas",
            1,
            ("9 Mixed deltas",),
            ("9",),
            10,
            10,
            " ".join(table_rows),
        )
        new_section = replace(old_section, section_id="new-cross-kind-refill", start_page=11, end_page=11)
        pairs = [
            SnippetPair(f"Old requirement {index} shall apply.", f"New requirement {index} shall apply.")
            for index in range(1, 4)
        ]
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            added_snippets=table_rows,
            replaced_snippets=pairs[:1],
            omitted_snippet_count=2,
            audit_added_snippets=table_rows,
            audit_removed_snippets=[],
            audit_replaced_snippets=pairs,
        )
        old_table = TableVisual(
            10,
            1,
            "Table 9-1. Refill values",
            (10.0, 20.0, 500.0, 700.0),
            "",
            table_rows,
            "structured rows",
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        new_table = replace(old_table, page_number=11)
        evidence = TableChange("modified", (old_table,), (new_table,), 1.0, False, ())

        cleaned = _reader_section_change(change, [evidence])

        self.assertIsNotNone(cleaned)
        self.assertEqual([], cleaned.added_snippets)
        self.assertEqual(pairs, cleaned.replaced_snippets)
        self.assertEqual(0, cleaned.omitted_snippet_count)

    def test_table_fragment_filter_reuses_cached_table_token_index(self) -> None:
        """Repeated snippet checks tokenize one large table body only once."""

        table_text = " ".join(
            f"Parameter P{index} Symbol S{index} Value {index} Units UI"
            for index in range(1000)
        )
        snippets = [f"Parameter P{index} Symbol S{index} Value {index} Units UI" for index in range(40)]
        reporting_module._reader_table_text_index.cache_clear()

        reporting_module._reader_filter_evidenced_table_fragments(snippets, table_text)
        cache_info = reporting_module._reader_table_text_index.cache_info()

        self.assertEqual(1, cache_info.misses)
        self.assertGreaterEqual(cache_info.hits, len(snippets) - 1)

    def test_single_side_section_skips_paired_table_fragment_proof(self) -> None:
        """Added/deleted sections have no paired coordinates and must never hit the proof assert."""

        new_section = Section(
            "new-only",
            "9 New section",
            "New section",
            1,
            ("9 New section",),
            ("9",),
            11,
            11,
            "The receiver shall support the new operating mode.",
        )
        change = SectionChange(
            "added",
            None,
            new_section,
            0.0,
            added_snippets=[new_section.body],
        )

        # 即使调用方同时传入其它表格证据，单侧章节仍原样进入读者报告且不抛断言。
        cleaned = _reader_section_change(change, [])
        self.assertIsNotNone(cleaned)
        self.assertEqual([new_section.body], cleaned.added_snippets)

    def test_coordinate_table_dedup_preserves_changed_normative_tail(self) -> None:
        """A real prose edit after a table wall must never be hidden with the table."""

        table_body = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level Label "
            "Description Reference First Last "
            + " ".join(str(value) for value in range(1, 101))
        )
        old_snippet = table_body + " Where port 2 shall remain disabled."
        new_snippet = table_body + " Where port 2 shall remain enabled."
        old_section = Section(
            "old-table-tail",
            "9 Coordinate Tail",
            "Coordinate Tail",
            1,
            ("9 Coordinate Tail",),
            ("9",),
            10,
            10,
            old_snippet,
        )
        new_section = Section(
            "new-table-tail",
            "9 Coordinate Tail",
            "Coordinate Tail",
            1,
            ("9 Coordinate Tail",),
            ("9",),
            11,
            11,
            new_snippet,
        )
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            replaced_snippets=[SnippetPair(old_snippet, new_snippet)],
        )
        old_table = TableVisual(
            10,
            1,
            "Table 9-1. Coordinate-backed values",
            (10.0, 20.0, 500.0, 700.0),
            "",
            [table_body],
            "structured rows",
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "page_number": 11,
            }
        )
        evidence = TableChange(
            "modified",
            (old_table,),
            (new_table,),
            1.0,
            False,
            (),
        )

        self.assertFalse(
            _reader_change_is_coordinate_proven_table_body_duplicate(
                change,
                [evidence],
            )
        )

    def test_coordinate_table_dedup_accepts_one_proven_cross_pair_prose_tail(self) -> None:
        """A sentence shifted behind a table tail must not recreate the raw table wall."""

        table_body = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level Label "
            "Description Reference First Last "
            + " ".join(str(value) for value in range(1, 101))
        )
        moved_table_tail = (
            "Transition Threshold Level Symbol Reference "
            + " ".join(str(value) for value in range(101, 141))
        )
        prose = (
            "It is acceptable to meet the receiver requirement with either supported "
            "test pattern."
        )
        old_first = f"{table_body} {moved_table_tail}"
        new_first = table_body
        old_second = prose
        new_second = f"{moved_table_tail} {prose}"
        old_section = Section(
            "old-cross-pair-tail",
            "9 Coordinate Tail",
            "Coordinate Tail",
            1,
            ("9 Coordinate Tail",),
            ("9",),
            10,
            10,
            f"{old_first} {old_second}",
        )
        new_section = Section(
            "new-cross-pair-tail",
            "9 Coordinate Tail",
            "Coordinate Tail",
            1,
            ("9 Coordinate Tail",),
            ("9",),
            11,
            11,
            f"{new_first} {new_second}",
        )
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            replaced_snippets=[
                SnippetPair(old_first, new_first),
                SnippetPair(old_second, new_second),
            ],
        )
        old_table = TableVisual(
            10,
            1,
            "Table 9-1. Coordinate-backed values",
            (10.0, 20.0, 500.0, 700.0),
            "",
            [f"{table_body} {moved_table_tail} {prose}"],
            "structured rows",
            content_fully_represented=True,
            row_alignment_reliable=False,
        )
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "page_number": 11,
                "row_texts": [f"{table_body} {moved_table_tail} {prose}"],
            }
        )
        evidence = TableChange(
            "review",
            (old_table,),
            (new_table,),
            1.0,
            False,
            (
                TableRowChange(
                    "表格行归属",
                    "多行单元格归属未验证",
                    "多行单元格归属未验证",
                    "需人工复核",
                ),
            ),
        )

        self.assertEqual(
            old_second,
            _reader_visible_prose_tail(new_second),
        )
        self.assertTrue(
            _reader_change_is_coordinate_proven_table_body_duplicate(
                change,
                [evidence],
            )
        )
        changed = replace(
            change,
            replaced_snippets=[
                SnippetPair(old_first, new_first),
                SnippetPair(old_second, new_second.replace("acceptable", "required")),
            ],
        )
        self.assertFalse(
            _reader_change_is_coordinate_proven_table_body_duplicate(
                changed,
                [evidence],
            )
        )
        reordered = replace(
            change,
            replaced_snippets=[
                SnippetPair(old_first, new_first),
                SnippetPair(
                    old_second,
                    new_second.replace("either supported", "supported either"),
                ),
            ],
        )
        self.assertEqual(
            reporting_module._reader_nonspace_character_counts(
                " ".join(pair.old for pair in reordered.replaced_snippets)
            ),
            reporting_module._reader_nonspace_character_counts(
                " ".join(pair.new for pair in reordered.replaced_snippets)
            ),
        )
        self.assertFalse(
            _reader_change_is_coordinate_proven_table_body_duplicate(
                reordered,
                [evidence],
            )
        )
        uncovered_old_table = replace(
            old_table,
            row_texts=[f"{table_body} {prose}"],
        )
        uncovered_new_table = replace(
            new_table,
            row_texts=[f"{table_body} {prose}"],
        )
        uncovered_evidence = replace(
            evidence,
            old_tables=(uncovered_old_table,),
            new_tables=(uncovered_new_table,),
        )
        self.assertFalse(
            _reader_change_is_coordinate_proven_table_body_duplicate(
                change,
                [uncovered_evidence],
            )
        )

    def test_coordinate_table_dedup_preserves_unrecognized_and_short_residual_edits(self) -> None:
        """Residual prose and limits survive even when they are too short for tail heuristics."""

        table_body = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level Label "
            "Description Reference First Last "
            + " ".join(str(value) for value in range(1, 121))
        )
        table = TableVisual(
            10,
            1,
            "Table 9-1. Coordinate-backed values",
            (10.0, 20.0, 500.0, 700.0),
            "",
            [table_body],
            "structured rows",
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        new_table = TableVisual(**{**table.__dict__, "page_number": 11})
        evidence = TableChange("modified", (table,), (new_table,), 1.0, False, ())
        for old_tail, new_tail in (
            ("The receiver supports CTLE mode.", "The receiver supports DFE mode."),
            ("Maximum 1 UI.", "Maximum 2 UI."),
            ("Limit = 10%", "Limit = 10"),
            ("α = 10", "β = 10"),
            ("x² = 10", "x³ = 10"),
            ("Maximum 1.0 UI", "Maximum 1 0 UI"),
            ("SNR_TX = 10", "SNR_tx = 10"),
            ("Custom glyph \uf067 = 10", "Custom glyph γ = 10"),
        ):
            with self.subTest(old_tail=old_tail):
                old_text = f"{table_body} {old_tail}"
                new_text = f"{table_body} {new_tail}"
                old_section = Section(
                    "old-residual",
                    "9 Residual",
                    "Residual",
                    1,
                    ("9 Residual",),
                    ("9",),
                    10,
                    10,
                    old_text,
                )
                new_section = Section(
                    "new-residual",
                    "9 Residual",
                    "Residual",
                    1,
                    ("9 Residual",),
                    ("9",),
                    11,
                    11,
                    new_text,
                )
                change = SectionChange(
                    "modified",
                    old_section,
                    new_section,
                    0.99,
                    replaced_snippets=[SnippetPair(old_text, new_text)],
                )
                self.assertFalse(
                    _reader_change_is_coordinate_proven_table_body_duplicate(
                        change,
                        [evidence],
                    )
                )

    def test_coordinate_table_dedup_combines_multiple_complete_tables(self) -> None:
        """Two proven tables may jointly explain a reordered section table wall."""

        header = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level "
            "Label Description Reference First Last"
        )
        first_body = f"{header} " + " ".join(f"A{value}" for value in range(1, 56))
        second_body = f"{header} " + " ".join(f"B{value}" for value in range(1, 56))
        old_text = f"{first_body} {second_body}"
        new_text = f"{second_body} {first_body}"
        old_section = Section(
            "old-two-tables",
            "9 Two tables",
            "Two tables",
            1,
            ("9 Two tables",),
            ("9",),
            10,
            10,
            old_text,
        )
        new_section = Section(
            "new-two-tables",
            "9 Two tables",
            "Two tables",
            1,
            ("9 Two tables",),
            ("9",),
            11,
            11,
            new_text,
        )
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            replaced_snippets=[SnippetPair(old_text, new_text)],
        )

        def evidence(title: str, body: str, number: int) -> TableChange:
            old_table = TableVisual(
                10,
                number,
                title,
                (10.0, 20.0, 500.0, 300.0),
                "",
                [body],
                "structured rows",
                content_fully_represented=True,
                row_alignment_reliable=True,
            )
            new_table = TableVisual(**{**old_table.__dict__, "page_number": 11})
            return TableChange("modified", (old_table,), (new_table,), 1.0, False, ())

        table_evidence = [
            evidence("Table 9-1. First values", first_body, 1),
            evidence("Table 9-2. Second values", second_body, 2),
        ]

        self.assertTrue(
            _reader_change_is_coordinate_proven_table_body_duplicate(
                change,
                table_evidence,
            )
        )

    def test_formula_operand_reordering_is_not_layout_only(self) -> None:
        """Equal token counts cannot turn B/C into the semantically different C/B."""

        old_formula = "A = B / C " + " + ".join(f"x{index}" for index in range(40))
        new_formula = "A = C / B " + " + ".join(f"x{index}" for index in range(40))

        self.assertEqual("layout", _reader_snippet_collapse_kind(old_formula))
        self.assertEqual("layout", _reader_snippet_collapse_kind(new_formula))
        self.assertFalse(
            reporting_module._reader_pair_is_layout_token_reorder(
                old_formula,
                new_formula,
            )
        )

    def test_row_value_reassignment_is_not_downgraded_to_layout_reorder(self) -> None:
        """A token multiset cannot hide numeric values moving between row identities."""

        old_table_text = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level "
            "Label Description Reference First Last "
            "RowA XA 1 dB RowB XB 2 UI "
            + " ".join(str(value) for value in range(30))
        )
        new_table_text = old_table_text.replace(
            "RowA XA 1 dB RowB XB 2 UI",
            "RowA XA 2 dB RowB XB 1 UI",
        )
        old_section = Section(
            "old-1", "1 Limits", "Limits", 1, ("1 Limits",), ("1",), 1, 1,
            old_table_text,
        )
        new_section = Section(
            "new-1", "1 Limits", "Limits", 1, ("1 Limits",), ("1",), 1, 1,
            new_table_text,
        )
        change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            replaced_snippets=[SnippetPair(old_table_text, new_table_text)],
        )

        self.assertFalse(
            reporting_module._reader_pair_is_layout_token_reorder(
                old_table_text,
                new_table_text,
            )
        )
        reader_change = _reader_section_change(change)
        self.assertIsNotNone(reader_change)
        self.assertEqual("modified", reader_change.change_type)

    def test_unchanged_coordinate_table_removes_reader_duplicate_but_keeps_audit(self) -> None:
        """An unchanged paired table is valid dedup evidence even though it has no table card."""

        old_snippet = (
            "Parameter Symbol Value Units Conditions Index Transition Threshold Level Label "
            "Description Reference First Last "
            + " ".join(str(value) for value in range(1, 101))
        )
        new_snippet = (
            "Value Units Parameter Symbol Conditions Index Transition Threshold Level Label "
            "Description Reference First Last "
            + " ".join(str(value) for value in range(1, 101))
        )
        old_section = Section(
            "old-unchanged-table",
            "9 Unchanged Coordinate Table",
            "Unchanged Coordinate Table",
            1,
            ("9 Unchanged Coordinate Table",),
            ("9",),
            10,
            10,
            old_snippet,
        )
        new_section = Section(
            "new-unchanged-table",
            "9 Unchanged Coordinate Table",
            "Unchanged Coordinate Table",
            1,
            ("9 Unchanged Coordinate Table",),
            ("9",),
            11,
            11,
            new_snippet,
        )
        raw_change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            replaced_snippets=[SnippetPair(old_snippet, new_snippet)],
        )
        old_table = TableVisual(
            10,
            1,
            "Table 9-1. Stable values",
            (10.0, 20.0, 500.0, 700.0),
            "",
            [old_snippet],
            "structured rows",
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "page_number": 11,
            }
        )
        result = DiffResult(
            Path("old.pdf"),
            Path("new.pdf"),
            [old_section],
            [new_section],
            [raw_change],
            [],
            old_table_visuals=[old_table],
            new_table_visuals=[new_table],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            rendered = {
                kind: outputs[kind].read_text(encoding="utf-8")
                for kind in ("html", "markdown", "text")
            }
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
            csv_text = outputs["csv"].read_text(encoding="utf-8-sig")

        for kind, report in rendered.items():
            with self.subTest(kind=kind):
                self.assertNotIn("9 Unchanged Coordinate Table", report)
        self.assertEqual("modified", payload["changes"][0]["change_type"])
        self.assertIn("9 Unchanged Coordinate Table", csv_text)

    def test_reader_completeness_flag_requires_lossless_structured_rows(self) -> None:
        """Exact bbox characters cannot authorize dedup when final rows lost a NOTE."""

        rows = [["Parameter", "Value"], ["Limit", "10"]]

        class FakeTable:
            bbox = (0.0, 0.0, 100.0, 100.0)

            @staticmethod
            def extract() -> list[list[str]]:
                return rows

        class FakePage:
            @staticmethod
            def find_tables() -> list[FakeTable]:
                return [FakeTable()]

        def visual_builder(
            _page: object,
            _table: object,
            table_lines: list[str],
            page_number: int,
            table_number: int,
            **kwargs: object,
        ) -> tuple[TableVisual, str]:
            return (
                TableVisual(
                    page_number,
                    table_number,
                    "Table 1. Limits",
                    FakeTable.bbox,
                    "",
                    table_lines,
                    "structured rows",
                    content_fully_represented=bool(
                        kwargs["content_fully_represented"]
                    ),
                ),
                "",
            )

        with (
            mock.patch.object(pdf_extract_module, "_table_cell_word_rows", return_value=None),
            mock.patch.object(
                pdf_extract_module,
                "_table_lines_from_rows_with_data_evidence",
                return_value=(
                    ["表格行: T1 | Parameter=Limit | Value=10"],
                    False,
                    False,
                    False,
                ),
            ),
            mock.patch.object(
                pdf_extract_module,
                "_table_title_above_bbox",
                return_value="Table 1. Limits",
            ),
            mock.patch.object(
                pdf_extract_module,
                "_table_bbox_content_is_fully_represented",
                return_value=True,
            ),
            mock.patch.object(
                pdf_extract_module,
                "_fully_represented_table_row_bboxes",
                return_value=[],
            ),
            mock.patch.object(
                pdf_extract_module,
                "_build_table_visual",
                side_effect=visual_builder,
            ),
        ):
            _lines, visuals, _warnings, _covered = (
                pdf_extract_module._extract_table_lines_and_visuals(
                    FakePage(),
                    "sample.pdf",
                    1,
                )
            )

        self.assertEqual(1, len(visuals))
        self.assertFalse(visuals[0].content_fully_represented)

    def test_explicit_output_state_variants_expand_without_splitting_soft_wraps(self) -> None:
        """A dash-labelled closed-state pair is two rows, not one wrapped descriptor."""

        lines = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Min", "Max", "Unit", "Conditions"],
                [
                    "Differential Voltage pk-pk — Output Disabled\nOutput Enabled",
                    "-\n-",
                    "30\n1000",
                    "mV\nmV",
                    "See 31.3.4",
                ],
            ],
            1,
        )

        self.assertEqual(2, len(lines))
        self.assertIn(
            "Parameter=Differential Voltage pk-pk — Output Disabled | "
            "Min=- | Max=30 | Unit=mV | Conditions=See 31.3.4",
            lines[0],
        )
        self.assertIn(
            "Parameter=Output Enabled | Min=- | Max=1000 | Unit=mV | "
            "Conditions=See 31.3.4",
            lines[1],
        )

        soft_wrap = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Min", "Max"],
                ["Limit — Maximum value\nMinimum value", "1\n2", "10\n20"],
            ],
            2,
        )
        self.assertEqual(1, len(soft_wrap))

    def test_lossless_table_content_is_distinct_from_uncertain_row_alignment(self) -> None:
        """Character conservation alone cannot authorize whole-table removal."""

        rows = [
            ["Parameter", "Symbol", "Value"],
            ["Limit A\nLimit B", "X\nY", "1\n2\n3"],
        ]
        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                rows,
                1,
            )
        )

        self.assertEqual(1, len(lines))
        self.assertTrue(content_lossless)
        self.assertFalse(alignment_reliable)

        class FakeCrop:
            @staticmethod
            def extract_text(**_kwargs: object) -> str:
                return "\n".join(cell for row in rows for cell in row)

        class FakePage:
            @staticmethod
            def crop(_bbox: tuple[float, float, float, float]) -> FakeCrop:
                return FakeCrop()

        self.assertFalse(
            pdf_extract_module._table_bbox_is_fully_represented(
                FakePage(),
                (0.0, 0.0, 100.0, 100.0),
                rows,
                row_content_fully_represented=content_lossless,
                row_alignment_reliable=alignment_reliable,
            )
        )

    def test_conflicting_cell_baselines_revoke_table_row_alignment(self) -> None:
        """Typed columns cannot override observed cross-column y misalignment."""

        rows = [
            ["Parameter", "Symbol", "Value", "Units"],
            ["Setting A\nSetting B", "X\nY", "1\n2", "dB\ndB"],
        ]

        def word(text: str, top: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": 0.0,
                "x1": float(max(len(text), 1) * 6),
                "top": top,
                "bottom": top + 10.0,
                "size": 10.0,
            }

        header_words: list[list[dict[str, object]]] = [[], [], [], []]

        def evidence(offset_structured_columns: bool) -> list[list[list[dict[str, object]]]]:
            descriptor_tops = (100.0, 140.0)
            other_tops = (140.0, 180.0) if offset_structured_columns else descriptor_tops
            return [
                header_words,
                [
                    [word("Setting A", descriptor_tops[0]), word("Setting B", descriptor_tops[1])],
                    [word("X", other_tops[0]), word("Y", other_tops[1])],
                    [word("1", other_tops[0]), word("2", other_tops[1])],
                    [word("dB", other_tops[0]), word("dB", other_tops[1])],
                ],
            ]

        for offset, expected_alignment in ((False, True), (True, False)):
            with self.subTest(offset=offset):
                lines, content_lossless, alignment_reliable = (
                    pdf_extract_module._table_lines_from_rows_with_evidence(
                        rows,
                        1,
                        cell_word_rows=evidence(offset),
                    )
                )
                self.assertEqual(1 if offset else 2, len(lines))
                self.assertTrue(content_lossless)
                self.assertEqual(expected_alignment, alignment_reliable)

    def test_descriptor_and_condition_soft_wraps_do_not_create_extra_rows(self) -> None:
        """Two free-text columns cannot by themselves prove multiple logical records."""

        rows = [
            ["Parameter", "Test Point", "Min", "Max", "Unit", "Conditions"],
            [
                "Differential Mode to Common Mode\nConversion (SCD11)",
                "TP1",
                "-",
                "Equation (31-1)",
                "dB",
                "See 31.3.8 and\nNote3",
            ],
            [
                "Differential Termination Resistance\nMismatch",
                "TP4a",
                "-",
                "10 %",
                "%",
                "At 1MHz. See\n13.3.6",
            ],
        ]

        lines = pdf_extract_module._table_lines_from_rows(rows, 1)

        self.assertEqual(2, len(lines))
        self.assertIn(
            "Parameter=Differential Mode to Common Mode\\nConversion (SCD11)",
            lines[0],
        )
        self.assertIn("Conditions=See 31.3.8 and\\nNote3", lines[0])
        self.assertIn(
            "Parameter=Differential Termination Resistance\\nMismatch",
            lines[1],
        )
        self.assertIn("Conditions=At 1MHz. See\\n13.3.6", lines[1])

    def test_lowercase_symbol_rows_do_not_merge_into_one_identifier(self) -> None:
        """Independent symbols a/b are records, not an inferred visual subscript."""

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                [
                    ["Parameter", "Symbol", "Value"],
                    ["Setting A\nSetting B", "a\nb", "1\n2"],
                ],
                1,
            )
        )

        self.assertEqual(2, len(lines))
        self.assertIn("Parameter=Setting A", lines[0])
        self.assertIn("Symbol=a", lines[0])
        self.assertIn("Value=1", lines[0])
        self.assertIn("Parameter=Setting B", lines[1])
        self.assertIn("Symbol=b", lines[1])
        self.assertIn("Value=2", lines[1])
        self.assertTrue(content_lossless)
        self.assertTrue(alignment_reliable)

    def test_ambiguous_lowercase_symbol_lines_do_not_equal_joined_identifier(self) -> None:
        """Without row evidence, observed ``a``/``b`` must stay distinct from ``ab``."""

        old_lines = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Symbol"],
                ["Setting", "a\nb"],
            ],
            1,
        )
        new_lines = pdf_extract_module._table_lines_from_rows(
            [
                ["Parameter", "Symbol"],
                ["Setting", "ab"],
            ],
            1,
        )

        self.assertNotEqual(old_lines, new_lines)
        self.assertIn("Symbol=a / b", old_lines[0])
        self.assertIn("Symbol=ab", new_lines[0])

    def test_wrapped_formula_value_does_not_authorize_row_expansion(self) -> None:
        """A Max formula split over lines is not independent record evidence."""

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                [
                    ["Parameter", "Max", "Conditions"],
                    [
                        "Mode A\nMode B",
                        "Equation\n(31-1)",
                        "See Note 1\ncontinued text",
                    ],
                ],
                1,
            )
        )

        self.assertEqual(1, len(lines))
        self.assertIn("Parameter=Mode A\\nMode B", lines[0])
        self.assertIn("Max=Equation\\n(31-1)", lines[0])
        self.assertIn("Conditions=See Note 1\\ncontinued text", lines[0])
        self.assertTrue(content_lossless)
        self.assertFalse(alignment_reliable)

    def test_trailing_formula_operator_does_not_authorize_row_expansion(self) -> None:
        """An operator on either side of a newline keeps the formula aggregated."""

        for formula in ("1 +\n2", "1\n+2"):
            with self.subTest(formula=formula):
                lines, content_lossless, alignment_reliable = (
                    pdf_extract_module._table_lines_from_rows_with_evidence(
                        [
                            ["Parameter", "Max"],
                            ["Maximum output\namplitude", formula],
                        ],
                        1,
                    )
                )

                self.assertEqual(1, len(lines))
                self.assertIn(r"Parameter=Maximum output\namplitude", lines[0])
                encoded_formula = formula.replace("\n", "\\n")
                self.assertIn(f"Max={encoded_formula}", lines[0])
                self.assertTrue(content_lossless)
                self.assertFalse(alignment_reliable)

    def test_symbol_or_unit_lines_alone_do_not_authorize_row_expansion(self) -> None:
        """Visual subscripts and wrapped unit words cannot prove logical records."""

        cases = (
            [
                ["Parameter", "Symbol"],
                ["Reference voltage\nlimit", "V\nREF"],
            ],
            [
                ["Characteristic", "Symbol", "Unit"],
                [
                    "Uncorrelated jitter RMS (standard deviation of\nthe probability distribution)",
                    "T_J\nRMS03",
                    "UI\nrms",
                ],
            ],
            [
                ["Characteristic", "Symbol", "Unit"],
                ["Maximum likelihood\nsequence detection", "Mode\nA", "Not\nSpecified"],
            ],
            [
                ["Parameter", "Symbol", "Value", "Units"],
                ["Reference voltage\nlimit", "V\nREF", "Maximum\nOutput", "m\nV"],
            ],
        )
        for rows in cases:
            with self.subTest(rows=rows):
                lines, content_lossless, alignment_reliable = (
                    pdf_extract_module._table_lines_from_rows_with_evidence(rows, 1)
                )
                self.assertEqual(1, len(lines))
                self.assertTrue(content_lossless)
                self.assertFalse(alignment_reliable)

    def test_one_numeric_column_plus_free_text_wraps_does_not_prove_rows(self) -> None:
        """Parameter/Max/Conditions line counts alone cannot prove two records."""

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                [
                    ["Parameter", "Max", "Conditions"],
                    ["Maximum output\namplitude", "1\n2", "At low\nfrequency"],
                ],
                1,
            )
        )

        self.assertEqual(1, len(lines))
        self.assertIn(r"Max=1\n2", lines[0])
        self.assertTrue(content_lossless)
        self.assertFalse(alignment_reliable)

    def test_unproven_extra_descriptor_is_not_guessed_as_group_label(self) -> None:
        """Five descriptors plus four values may mean a missing value, not a group row."""

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                [
                    ["Parameter", "Value", "Units"],
                    ["A\nB\nC\nD\nE", "1\n2\n3\n4", "dB\ndB\ndB\ndB"],
                ],
                1,
            )
        )

        self.assertEqual(1, len(lines))
        self.assertIn(r"Parameter=A\nB\nC\nD\nE", lines[0])
        self.assertTrue(content_lossless)
        self.assertFalse(alignment_reliable)

    def test_missing_middle_value_does_not_shift_following_rows(self) -> None:
        """Without y geometry, a target-minus-one column has an unknown gap position."""

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                [
                    ["Parameter", "Symbol", "Value", "Units"],
                    ["A\nB\nC", "X\nY\nZ", "1\n3", "dB\ndB\ndB"],
                ],
                1,
            )
        )

        self.assertEqual(1, len(lines))
        self.assertIn(r"Value=1\n3", lines[0])
        self.assertTrue(content_lossless)
        self.assertFalse(alignment_reliable)

    def test_missing_unit_without_geometry_stays_aggregated(self) -> None:
        """A target-minus-one Units column cannot assume the blank belongs to the last row."""

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                [
                    ["Parameter", "Symbol", "Value", "Units"],
                    ["First\nSecond\nThird", "A\nB\nC", "1\n2\n3", "UI\nUI"],
                ],
                1,
            )
        )

        self.assertEqual(1, len(lines))
        self.assertIn(r"Parameter=First\nSecond\nThird", lines[0])
        self.assertIn(r"Units=UI\nUI", lines[0])
        self.assertTrue(content_lossless)
        self.assertFalse(alignment_reliable)

    def test_missing_unit_uses_geometry_to_place_the_blank_row(self) -> None:
        """Observed y baselines may prove that the first, not the last, unit is blank."""

        def word(text: str, top: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": 0.0,
                "x1": 10.0,
                "top": top,
                "bottom": top + 10.0,
                "size": 10.0,
            }

        tops = (100.0, 120.0, 140.0)
        rows = [
            ["Parameter", "Symbol", "Value", "Units"],
            ["First\nSecond\nThird", "A\nB\nC", "1\n2\n3", "UI\nUI"],
        ]
        words = [
            [[], [], [], []],
            [
                [word(text, top) for text, top in zip(("First", "Second", "Third"), tops)],
                [word(text, top) for text, top in zip(("A", "B", "C"), tops)],
                [word(text, top) for text, top in zip(("1", "2", "3"), tops)],
                [word(text, top) for text, top in zip(("UI", "UI"), tops[1:])],
            ],
        ]

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                rows,
                1,
                cell_word_rows=words,
            )
        )

        self.assertEqual(3, len(lines))
        self.assertIn("Parameter=First", lines[0])
        self.assertIn("Units=", lines[0])
        self.assertIn("Parameter=Second", lines[1])
        self.assertIn("Units=UI", lines[1])
        self.assertIn("Parameter=Third", lines[2])
        self.assertIn("Units=UI", lines[2])
        self.assertTrue(content_lossless)
        self.assertTrue(alignment_reliable)

    def test_group_label_trailing_sparse_unit_with_script_words_stays_review_only(self) -> None:
        """An ordered sparse prefix stays readable without certifying the blank slot."""

        def word(
            text: str,
            top: float,
            *,
            x0: float = 0.0,
            height: float = 10.0,
        ) -> dict[str, object]:
            return {
                "text": text,
                "x0": x0,
                "x1": x0 + float(max(len(text), 1) * 6),
                "top": top,
                "bottom": top + height,
                "size": height,
            }

        record_tops = (100.0, 120.0, 140.0)
        rows = [
            ["Parameter", "Symbol", "Value", "Units"],
            [
                "Device group: modes\nFirst\nSecond\nThird",
                "Z\nc\nZ\nc2\nZ\nc3",
                "1x10-4\n2x10-4\n3x10-4",
                "UI\nUI",
            ],
        ]

        def symbol_words() -> list[dict[str, object]]:
            values: list[dict[str, object]] = []
            for suffix, top in zip(("c", "c2", "c3"), record_tops, strict=True):
                values.extend(
                    [
                        word("Z", top),
                        word(suffix, top + 3.0, x0=6.0, height=7.0),
                    ]
                )
            return values

        def value_words() -> list[dict[str, object]]:
            values: list[dict[str, object]] = []
            for lead, top in zip(("1x10", "2x10", "3x10"), record_tops, strict=True):
                values.extend(
                    [
                        word(lead, top, x0=0.0),
                        word("-4", top - 2.0, x0=24.0, height=7.0),
                    ]
                )
            return values

        def evidence(unit_tops: tuple[float, float]) -> list[list[list[dict[str, object]]]]:
            return [
                [[], [], [], []],
                [
                    [
                        word("Device group: modes", 80.0),
                        *[
                            word(text, top)
                            for text, top in zip(
                                ("First", "Second", "Third"),
                                record_tops,
                                strict=True,
                            )
                        ],
                    ],
                    symbol_words(),
                    value_words(),
                    [word("UI", top) for top in unit_tops],
                ],
            ]

        trailing_lines, trailing_lossless, trailing_alignment = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                rows,
                1,
                cell_word_rows=evidence((100.0, 126.0)),
            )
        )
        middle_gap_lines, middle_gap_lossless, middle_gap_alignment = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                rows,
                1,
                cell_word_rows=evidence((100.0, 140.0)),
            )
        )

        self.assertEqual(3, len(trailing_lines))
        self.assertIn("Parameter=Device group: modes\\nFirst", trailing_lines[0])
        self.assertIn("Symbol=Zc", trailing_lines[0])
        self.assertIn("Value=1x10-4", trailing_lines[0])
        self.assertIn("Units=UI", trailing_lines[0])
        self.assertIn("Units=UI", trailing_lines[1])
        self.assertIn("Units=", trailing_lines[2])
        self.assertNotIn("Units=UI", trailing_lines[2])
        self.assertTrue(trailing_lossless)
        self.assertFalse(trailing_alignment)

        self.assertEqual(1, len(middle_gap_lines))
        self.assertIn(r"Units=UI\nUI", middle_gap_lines[0])
        self.assertTrue(middle_gap_lossless)
        self.assertFalse(middle_gap_alignment)

    def test_group_label_sparse_unit_rejects_nonuniform_middle_gap_as_reliable(self) -> None:
        """A boundary-positioned Unit cannot be certified as a continuous prefix."""

        def word(text: str, top: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": 0.0,
                "x1": 10.0,
                "top": top,
                "bottom": top + 10.0,
                "size": 10.0,
            }

        rows = [
            ["Parameter", "Symbol", "Value", "Units"],
            [
                "Device group: modes\nFirst\nSecond\nThird",
                "A\nB\nC",
                "1\n2\n3",
                "UI\nUI",
            ],
        ]
        record_tops = (100.0, 120.0, 131.0)
        words = [
            [[], [], [], []],
            [
                [
                    word("Device group: modes", 80.0),
                    *[
                        word(text, top)
                        for text, top in zip(
                            ("First", "Second", "Third"),
                            record_tops,
                            strict=True,
                        )
                    ],
                ],
                [
                    word(text, top)
                    for text, top in zip(("A", "B", "C"), record_tops, strict=True)
                ],
                [
                    word(text, top)
                    for text, top in zip(("1", "2", "3"), record_tops, strict=True)
                ],
                [word("UI", 100.0), word("UI", 126.0)],
            ],
        ]

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                rows,
                1,
                cell_word_rows=words,
            )
        )

        self.assertEqual(3, len(lines))
        self.assertTrue(content_lossless)
        self.assertFalse(alignment_reliable)

    def test_singleton_condition_uses_unique_row_geometry_instead_of_broadcast(self) -> None:
        """Moving one Condition between rows must change its structured association."""

        def word(text: str, top: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": 0.0,
                "x1": 10.0,
                "top": top,
                "bottom": top + 10.0,
                "size": 10.0,
            }

        rows = [
            ["Parameter", "Symbol", "Value", "Units", "Conditions"],
            [
                "P1\nP2\nP3",
                "A\nB\nC",
                "1\n2\n3",
                "V\nV\nV",
                "At low frequency",
            ],
        ]
        record_tops = (100.0, 120.0, 140.0)

        def evidence(condition_top: float) -> list[list[list[dict[str, object]]]]:
            return [
                [[], [], [], [], []],
                [
                    [word(text, top) for text, top in zip(("P1", "P2", "P3"), record_tops, strict=True)],
                    [word(text, top) for text, top in zip(("A", "B", "C"), record_tops, strict=True)],
                    [word(text, top) for text, top in zip(("1", "2", "3"), record_tops, strict=True)],
                    [word("V", top) for top in record_tops],
                    [word("At low frequency", condition_top)],
                ],
            ]

        first_lines, first_lossless, first_alignment = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                rows,
                1,
                cell_word_rows=evidence(100.0),
            )
        )
        last_lines, last_lossless, last_alignment = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                rows,
                1,
                cell_word_rows=evidence(140.0),
            )
        )

        self.assertIn("Conditions=At low frequency", first_lines[0])
        self.assertNotIn("Conditions=At low frequency", first_lines[1])
        self.assertNotIn("Conditions=At low frequency", first_lines[2])
        self.assertNotIn("Conditions=At low frequency", last_lines[0])
        self.assertNotIn("Conditions=At low frequency", last_lines[1])
        self.assertIn("Conditions=At low frequency", last_lines[2])
        self.assertNotEqual(first_lines, last_lines)
        self.assertTrue(first_lossless)
        self.assertTrue(last_lossless)
        self.assertFalse(first_alignment)
        self.assertFalse(last_alignment)

        old_table = TableVisual(
            1,
            1,
            "Table 1. Conditions",
            (0.0, 0.0, 100.0, 100.0),
            "",
            first_lines,
            "rows",
            content_fully_represented=True,
            row_alignment_reliable=first_alignment,
        )
        new_table = TableVisual(
            1,
            1,
            "Table 1. Conditions",
            (0.0, 0.0, 100.0, 100.0),
            "",
            last_lines,
            "rows",
            content_fully_represented=True,
            row_alignment_reliable=last_alignment,
        )
        table_changes = reporting_module._build_table_changes(
            DiffResult(
                Path("old.pdf"),
                Path("new.pdf"),
                [],
                [],
                [],
                [],
                old_table_visuals=[old_table],
                new_table_visuals=[new_table],
            )
        )
        self.assertTrue(table_changes)
        self.assertTrue(
            all(
                row.change_type == "需人工复核"
                for change in table_changes
                for row in change.row_changes
            )
        )

    def test_boundary_singleton_condition_fails_closed(self) -> None:
        """A Condition between two row baselines must not certify repeated ownership."""

        def word(text: str, top: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": 0.0,
                "x1": 10.0,
                "top": top,
                "bottom": top + 10.0,
                "size": 10.0,
            }

        tops = (100.0, 120.0, 131.0)
        rows = [
            ["Parameter", "Symbol", "Value", "Units", "Conditions"],
            ["P1\nP2\nP3", "A\nB\nC", "1\n2\n3", "V\nV\nV", "At low frequency"],
        ]
        words = [
            [[], [], [], [], []],
            [
                [word(text, top) for text, top in zip(("P1", "P2", "P3"), tops, strict=True)],
                [word(text, top) for text, top in zip(("A", "B", "C"), tops, strict=True)],
                [word(text, top) for text, top in zip(("1", "2", "3"), tops, strict=True)],
                [word("V", top) for top in tops],
                [word("At low frequency", 126.0)],
            ],
        ]

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                rows,
                1,
                cell_word_rows=words,
            )
        )

        self.assertEqual(3, len(lines))
        self.assertTrue(content_lossless)
        self.assertFalse(alignment_reliable)

    def test_single_middle_unit_uses_geometry_without_broadcast(self) -> None:
        """One Unit may be localized for display but stays review-only without rowspan proof."""

        def word(text: str, top: float) -> dict[str, object]:
            return {
                "text": text,
                "x0": 0.0,
                "x1": 10.0,
                "top": top,
                "bottom": top + 10.0,
                "size": 10.0,
            }

        tops = (100.0, 120.0, 140.0)
        rows = [
            ["Parameter", "Symbol", "Value", "Units"],
            ["First\nSecond\nThird", "A\nB\nC", "1\n2\n3", "UI"],
        ]
        words = [
            [[], [], [], []],
            [
                [word(text, top) for text, top in zip(("First", "Second", "Third"), tops)],
                [word(text, top) for text, top in zip(("A", "B", "C"), tops)],
                [word(text, top) for text, top in zip(("1", "2", "3"), tops)],
                [word("UI", tops[1])],
            ],
        ]

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                rows,
                1,
                cell_word_rows=words,
            )
        )
        new_lines, new_content_lossless, new_alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                [
                    rows[0],
                    ["First\nSecond\nThird", "A\nB\nC", "1\n2\n3", "UI\nUI\nUI"],
                ],
                1,
                cell_word_rows=[
                    words[0],
                    [
                        *words[1][:3],
                        [word("UI", top) for top in tops],
                    ],
                ],
            )
        )

        self.assertEqual(3, len(lines))
        self.assertEqual(3, len(new_lines))
        self.assertIn("Parameter=First", lines[0])
        self.assertIn("Units=", lines[0])
        self.assertNotIn("Units=UI", lines[0])
        self.assertIn("Parameter=Second", lines[1])
        self.assertIn("Units=UI", lines[1])
        self.assertIn("Parameter=Third", lines[2])
        self.assertIn("Units=", lines[2])
        self.assertNotIn("Units=UI", lines[2])
        self.assertEqual(1, sum("Units=UI" in line for line in lines))
        self.assertNotEqual(lines[0], new_lines[0])
        self.assertEqual(lines[1], new_lines[1])
        self.assertNotEqual(lines[2], new_lines[2])
        self.assertTrue(content_lossless)
        self.assertFalse(alignment_reliable)
        self.assertTrue(new_content_lossless)
        self.assertTrue(new_alignment_reliable)

        old_table = TableVisual(
            1,
            1,
            "Table 1. Units",
            (0.0, 0.0, 100.0, 100.0),
            "",
            lines,
            "rows",
            content_fully_represented=True,
            row_alignment_reliable=alignment_reliable,
        )
        new_table = TableVisual(
            1,
            1,
            "Table 1. Units",
            (0.0, 0.0, 100.0, 100.0),
            "",
            new_lines,
            "rows",
            content_fully_represented=True,
            row_alignment_reliable=new_alignment_reliable,
        )
        table_changes = reporting_module._build_table_changes(
            DiffResult(
                Path("old.pdf"),
                Path("new.pdf"),
                [],
                [],
                [],
                [],
                old_table_visuals=[old_table],
                new_table_visuals=[new_table],
            )
        )
        self.assertTrue(table_changes)
        self.assertTrue(
            all(
                row.change_type == "需人工复核"
                for change in table_changes
                for row in change.row_changes
            )
        )

    def test_single_unit_without_geometry_stays_aggregated(self) -> None:
        """One unit cannot be broadcast across records without row or rowspan evidence."""

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                [
                    ["Parameter", "Symbol", "Value", "Units"],
                    ["First\nSecond\nThird", "A\nB\nC", "1\n2\n3", "UI"],
                ],
                1,
            )
        )

        self.assertEqual(1, len(lines))
        self.assertIn(r"Parameter=First\nSecond\nThird", lines[0])
        self.assertIn("Units=UI", lines[0])
        self.assertTrue(content_lossless)
        self.assertFalse(alignment_reliable)

    def test_verified_compact_symbol_is_not_split_to_fill_other_rows(self) -> None:
        """Z/c remains Zc when other columns happen to contain two records."""

        lines, content_lossless, alignment_reliable = (
            pdf_extract_module._table_lines_from_rows_with_evidence(
                [
                    ["Parameter", "Symbol", "Value", "Units"],
                    ["Setting A\nSetting B", "Z\nc", "1\n2", "dB\ndB"],
                ],
                1,
            )
        )

        self.assertEqual(1, len(lines))
        self.assertIn("Symbol=Zc", lines[0])
        self.assertTrue(content_lossless)
        self.assertFalse(alignment_reliable)

    def test_three_neutral_columns_can_prove_aligned_generic_rows(self) -> None:
        """A headerless grid expands only with an atomic numeric record column."""

        lines = pdf_extract_module._table_lines_from_rows(
            [
                ["preset 1\npreset 2", "33.5\n27.5", "dB\ndB"],
            ],
            1,
        )

        self.assertEqual(2, len(lines))
        self.assertIn("Column 1=preset 1", lines[0])
        self.assertIn("Column 2=33.5", lines[0])
        self.assertIn("Column 1=preset 2", lines[1])
        self.assertIn("Column 2=27.5", lines[1])

    def test_three_neutral_soft_wrapped_prose_columns_stay_aggregated(self) -> None:
        """Equal line counts alone do not prove row alignment in an unknown schema."""

        cases = (
            [
                [
                    "Maximum likelihood\nsequence detection",
                    "See Note\ncontinued",
                    "Not\nSpecified",
                ]
            ],
            [
                [
                    "Receiver jitter RMS\nstandard deviation",
                    "T_J RMS03\ncontinued symbol",
                    "UI rms\nwrapped unit",
                ]
            ],
            [
                [
                    "Maximum output\namplitude",
                    "1\n+2",
                    "See Note\ncontinued",
                ]
            ],
        )
        for rows in cases:
            with self.subTest(rows=rows):
                lines, content_lossless, alignment_reliable = (
                    pdf_extract_module._table_lines_from_rows_with_evidence(rows, 1)
                )
                self.assertEqual(1, len(lines))
                self.assertTrue(content_lossless)
                self.assertFalse(alignment_reliable)

    def test_spanning_note_row_shows_the_real_sentence_change(self) -> None:
        """A note spanning the table must not degrade to identical empty fields."""

        old_note = (
            "The maximum likelihood sequence detection (MLSD) defined in "
            "178A.1.11 [2] is used for the calculation of COM."
        )
        new_note = old_note.replace(
            "defined in 178A.1.11",
            "defined in IEEE 802.3dj 178A.1.11",
        )
        change = _make_table_row_change(
            (
                "表格行: T1 | Parameter=The maximum likelihood sequence detection "
                "(MLSD) defined in\\n178A.1.11 [2] is used for the calculation of COM. "
                "| Symbol= | Value= | Units="
            ),
            (
                "表格行: T1 | Parameter=The maximum likelihood sequence detection "
                "(MLSD) defined in IEEE 802.3dj 178A.1.11 [2] is used for the\\n"
                "calculation of COM. | Symbol= | Value= | Units="
            ),
            "替换/修改",
        )

        self.assertEqual("表格说明", change.item)
        self.assertEqual(old_note, change.old_value)
        self.assertEqual(new_note, change.new_value)
        self.assertNotIn("↵", change.old_value + change.new_value)
        self.assertNotIn("<empty>", change.old_value + change.new_value)

    def test_reader_table_cells_hide_codec_newline_marker_only(self) -> None:
        """The audit keeps `↵`; reader output turns that codec marker into spacing."""

        raw = "peak- ↵ to-peak | Symbol=Z ↵ c"
        reader = reporting_module._reader_table_inline_text(raw)

        self.assertEqual("peak-to-peak | Symbol=Zc", reader)
        self.assertNotIn("↵", reader)

    def test_narrative_row_paired_with_data_row_preserves_both_sides(self) -> None:
        """A narrative shortcut cannot erase a newly paired structured data row."""

        old_row = (
            "表格行: T1 | Parameter=The maximum likelihood sequence detection is "
            "used for the calculation of COM. | Symbol= | Value= | Units="
        )
        new_row = (
            "表格行: T1 | Parameter=Threshold | Symbol=X | Value=1 | Units=dB"
        )

        changes = _ordered_table_row_changes([old_row], [new_row])

        self.assertEqual(1, len(changes))
        self.assertIn("maximum likelihood", changes[0].old_value)
        self.assertIn("Threshold", changes[0].new_value)
        self.assertIn("X", changes[0].new_value)
        self.assertIn("1 dB", changes[0].new_value)

    def test_common_empty_table_fields_are_omitted_but_schema_changes_remain_visible(self) -> None:
        """Shared empty MIN/TYP cells add noise; a newly added empty column is evidence."""

        common_empty_change = _make_table_row_change(
            (
                "表格行: T1 | Characteristic=Output jitter | Symbol=J3u | "
                "MIN= | TYP= | MAX=0.118 | UNIT=UI"
            ),
            (
                "表格行: T1 | Characteristic=Output jitter | Symbol=JH4u | "
                "MIN= | TYP= | MAX=0.118 | UNIT=UI"
            ),
            "实质/符号变化",
        )
        added_empty_schema = _make_table_row_change(
            "表格行: T1 | Parameter=Setting | Value=1",
            "表格行: T1 | Parameter=Setting | Value=1 | Notes=",
            "替换/修改",
        )
        deleted_row = _make_table_row_change(
            "表格行: T1 | Parameter=Old setting | Symbol= | Value=1 | Notes=",
            "",
            "旧表删除行",
        )

        self.assertNotIn(
            "<empty>",
            common_empty_change.old_value + common_empty_change.new_value,
        )
        self.assertIn("Notes=（空白）", added_empty_schema.new_value)
        self.assertNotIn("<empty>", added_empty_schema.new_value)
        self.assertNotIn("<empty>", deleted_row.old_value)

    def test_changed_parameter_name_is_visible_when_values_are_equal(self) -> None:
        """A wording change must not be hidden behind identical symbol/value cells."""

        change = _make_table_row_change(
            (
                "表格行: T1 | Parameter=FFE maximum span including floating taps | "
                "Symbol=Nf | Value=50 | Units=UI"
            ),
            (
                "表格行: T1 | Parameter=FFE maximum post-tap span including floating taps | "
                "Symbol=Nf | Value=50 | Units=UI"
            ),
            "替换/修改",
        )

        self.assertEqual("表格项目名称", change.item)
        self.assertIn("FFE maximum span", change.old_value)
        self.assertIn("FFE maximum post-tap span", change.new_value)
        self.assertNotEqual(change.old_value, change.new_value)

    def test_table_note_change_gets_inline_highlighting(self) -> None:
        """The actual inserted words should be immediately visible in the table card."""

        change = _make_table_row_change(
            (
                "表格行: T1 | Parameter=The detector defined in 178A.1.11 is used "
                "for the calculation of COM. | Symbol= | Value= | Units="
            ),
            (
                "表格行: T1 | Parameter=The detector defined in IEEE 802.3dj "
                "178A.1.11 is used for the calculation of COM. | Symbol= | Value= | Units="
            ),
            "替换/修改",
        )

        html = _render_table_row_change(change)

        self.assertIn('class="ins"', html)
        self.assertIn("IEEE", html)

    def test_generic_table_header_ignores_only_soft_wrap_separators(self) -> None:
        """`peak-to-peak` line wraps are equal while a literal slash stays semantic."""

        old_row = (
            "表格行: T1 | Column 1=Frequency Range | "
            "Column 2=Sinusoidal jitter, / peak-to-peak / (UI)"
        )
        for wrapped_header in (
            "Sinusoidal jitter, peak-to- / peak (UI)",
            "Sinusoidal jitter, peak- / to-peak (UI)",
        ):
            with self.subTest(wrapped_header=wrapped_header):
                new_row = (
                    "表格行: T1 | Column 1=Frequency Range | "
                    f"Column 2={wrapped_header}"
                )
                self.assertNotEqual(
                    "无变化",
                    _table_structured_diff_kind(old_row, new_row),
                )

        codec_old_row = pdf_extract_module._table_lines_from_rows(
            [["Frequency Range", "Sinusoidal jitter,\npeak-to-peak\n(UI)"]],
            1,
        )[0]
        for wrapped_header in (
            "Sinusoidal jitter, peak-to-\npeak (UI)",
            "Sinusoidal jitter, peak-\nto-peak (UI)",
        ):
            with self.subTest(codec_wrapped_header=wrapped_header):
                codec_new_row = pdf_extract_module._table_lines_from_rows(
                    [["Frequency Range", wrapped_header]],
                    1,
                )[0]
                self.assertEqual(
                    "无变化",
                    _table_structured_diff_kind(codec_old_row, codec_new_row),
                )

        self.assertNotEqual(
            "无变化",
            _table_structured_diff_kind(
                "表格行: T1 | Column 1=Mode | Column 2=A / B",
                "表格行: T1 | Column 1=Mode | Column 2=AB",
            ),
        )
        self.assertNotEqual(
            "无变化",
            _table_structured_diff_kind(
                (
                    "表格行: T1 | Column 1=Frequency Range | "
                    "Column 2=Pre-/post-cursor jitter"
                ),
                (
                    "表格行: T1 | Column 1=Frequency Range | "
                    "Column 2=Pre-post-cursor jitter"
                ),
            ),
        )
        self.assertNotEqual(
            "无变化",
            _table_structured_diff_kind(
                (
                    "表格行: T1 | Column 1=Parameter | "
                    "Column 2=Minimum / (Maximum when condition B applies)"
                ),
                (
                    "表格行: T1 | Column 1=Parameter | "
                    "Column 2=Minimum (Maximum when condition B applies)"
                ),
            ),
        )

    def test_generic_column_boundary_drift_remains_visible_without_grid_evidence(self) -> None:
        """Text alone cannot prove that fewer physical columns are extraction noise."""

        old_rows = [
            (
                "表格行: T1 | Column 1=Module Output Mode | "
                "Column 2=Host Channel Type | "
                "Column 3=Channel Insertion Loss / (dB) | Column 4=Zp (mm)"
            ),
            "表格行: T1 | Column 1=Short | Column 2=near-end | Column 3=0 | Column 4=0",
            "表格行: T1 | Column 1=Long | Column 2=far-end | Column 3=13 | Column 4=75",
        ]
        new_rows = [
            (
                "表格行: T1 | Column 1=Module Output Mode | "
                "Column 2=Host Channel Type / Channel Insertion Loss / (dB) | "
                "Column 3=Zp (mm)"
            ),
            "表格行: T1 | Column 1=Short | Column 2=near-end 0 | Column 3=0",
            "表格行: T1 | Column 1=Long | Column 2=far-end 13 | Column 3=75",
        ]

        self.assertTrue(_ordered_table_row_changes(old_rows, new_rows))

        reordered_new_rows = [
            new_rows[0],
            "表格行: T1 | Column 1=Short | Column 2=0 near-end | Column 3=0",
            "表格行: T1 | Column 1=Long | Column 2=13 far-end | Column 3=75",
        ]
        self.assertTrue(_ordered_table_row_changes(old_rows, reordered_new_rows))

        reordered_header = list(new_rows)
        reordered_header[0] = (
            "表格行: T1 | Column 1=Module Output Mode | "
            "Column 2=Channel Insertion Loss / Host Channel Type / (dB) | "
            "Column 3=Zp (mm)"
        )
        self.assertTrue(_ordered_table_row_changes(old_rows, reordered_header))

    def test_generic_header_field_reassignment_is_never_hidden_by_token_counts(self) -> None:
        """Same vocabulary cannot mask words moving across body-proven columns."""

        old_rows = [
            (
                "表格行: T1 | Column 1=Module Output Mode | "
                "Column 2=Host Channel Type | Column 3=Channel Insertion Loss | "
                "Column 4=Unit"
            ),
            "表格行: T1 | Column 1=Short | Column 2=near-end | Column 3=0 | Column 4=dB",
            "表格行: T1 | Column 1=Long | Column 2=far-end | Column 3=13 | Column 4=dB",
        ]
        reassigned_rows = [
            (
                "表格行: T1 | Column 1=Module Host Mode | "
                "Column 2=Output Channel Type | Column 3=Channel Insertion Loss Unit"
            ),
            "表格行: T1 | Column 1=Short near-end | Column 2=0 | Column 3=dB",
            "表格行: T1 | Column 1=Long far-end | Column 2=13 | Column 3=dB",
        ]
        reversed_schema_rows = [
            "表格行: T1 | Column 1=Parameter | Column 2=Maximum Minimum",
            "表格行: T1 | Column 1=A | Column 2=1 10",
            "表格行: T1 | Column 1=B | Column 2=2 20",
        ]
        min_max_rows = [
            "表格行: T1 | Column 1=Parameter | Column 2=Minimum | Column 3=Maximum",
            "表格行: T1 | Column 1=A | Column 2=1 | Column 3=10",
            "表格行: T1 | Column 1=B | Column 2=2 | Column 3=20",
        ]

        self.assertTrue(_ordered_table_row_changes(old_rows, reassigned_rows))
        self.assertTrue(_ordered_table_row_changes(min_max_rows, reversed_schema_rows))

        merged_schema_rows = [
            "表格行: T1 | Column 1=Parameter | Column 2=Minimum Maximum",
            "表格行: T1 | Column 1=A | Column 2=1 10",
            "表格行: T1 | Column 1=B | Column 2=2 20",
        ]
        self.assertTrue(_ordered_table_row_changes(min_max_rows, merged_schema_rows))

    def test_ordered_table_column_count_changes_scale_linearly_enough_for_review(self) -> None:
        """The production entry must not recompute whole-table merge evidence per row."""

        old_rows = [
            "表格行: T1 | Column 1=Parameter | Column 2=Mode | Column 3=Min | Column 4=Max"
        ]
        new_rows = [
            "表格行: T1 | Column 1=Parameter | Column 2=Mode Min | Column 3=Max"
        ]
        for index in range(80):
            old_rows.append(
                f"表格行: T1 | Column 1=P{index} | Column 2=M{index} | "
                f"Column 3={index} | Column 4={index + 1}"
            )
            new_rows.append(
                f"表格行: T1 | Column 1=P{index} | Column 2=M{index} {index} | "
                f"Column 3={index + 1}"
            )

        started = time.monotonic()
        changes = _ordered_table_row_changes(old_rows, new_rows)
        elapsed = time.monotonic() - started

        self.assertEqual(81, len(changes))
        self.assertLess(elapsed, 2.0)

    def test_generic_boundary_matching_is_bounded_for_wide_and_duplicate_rows(self) -> None:
        """Wide tables use prefix DP and ambiguous partitions fail closed."""

        source = [
            (index, f"Column {index + 1}", f"column {index + 1}", f"v{index}")
            for index in range(20)
        ]
        target = [
            (index, f"Column {index + 1}", f"column {index + 1}", f"v{2 * index} v{2 * index + 1}")
            for index in range(10)
        ]
        started = time.monotonic()
        patterns = _generic_boundary_merge_patterns_for_entries(source, target)
        elapsed = time.monotonic() - started

        self.assertEqual(1, len(patterns))
        self.assertLess(elapsed, 1.0)
        ambiguous_source = [
            (index, f"Column {index + 1}", f"column {index + 1}", "")
            for index in range(20)
        ]
        ambiguous_target = [
            (index, f"Column {index + 1}", f"column {index + 1}", "")
            for index in range(10)
        ]
        self.assertEqual(
            set(),
            _generic_boundary_merge_patterns_for_entries(
                ambiguous_source,
                ambiguous_target,
            ),
        )

    def test_standalone_table_reference_is_hidden_when_full_sentence_has_same_change(self) -> None:
        """审计保留所有表号替换，读者隐藏整句和孤立引用中的纯表号变化。"""

        old_text = (
            "1 Receiver requirements\n"
            "Further receiver electrical requirements are specified in Table 32-9, "
            "with interference tolerance parameters specified in Table 32-10.\n"
            "Table 32-9.\n"
            "Table 32-10."
        )
        new_text = old_text.replace("32-9", "32-7").replace("32-10", "32-8")
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            DiffOptions(),
        )

        pairs = [pair for change in result.changes for pair in change.replaced_snippets]
        self.assertTrue(any("Further receiver electrical" in pair.old for pair in pairs))
        self.assertTrue(
            any(
                pair.old.strip() in {"Table 32-9.", "Table 32-10."}
                for pair in pairs
            )
        )
        reader_changes = [
            reader_change
            for change in result.changes
            if (reader_change := _reader_section_change(change)) is not None
        ]
        reader_pairs = [
            pair
            for change in reader_changes
            for pair in change.replaced_snippets
        ]
        # 完整句和孤立引用都只改变显式 Table 编号，因此不再占用读者差异列表。
        self.assertFalse(reader_pairs)

    def test_standalone_table_renumber_is_reader_hidden_when_table_card_proves_it(self) -> None:
        """A paired table card replaces a context-free renumber only in reader output."""

        old_section = Section(
            "old-1", "1 Limits", "Limits", 1, ("1 Limits",), ("1",), 1, 1, "",
        )
        new_section = Section(
            "new-1", "1 Limits", "Limits", 1, ("1 Limits",), ("1",), 1, 1, "",
        )
        raw_change = SectionChange(
            "modified",
            old_section,
            new_section,
            0.99,
            replaced_snippets=[SnippetPair("Table 32-9.", "Table 32-7.")],
        )
        old_table = TableVisual(
            1,
            1,
            "Table 32-9. Receiver limits",
            (10.0, 10.0, 100.0, 100.0),
            "",
            ["表格行: T1 | Parameter=Limit | Value=1"],
            "grid",
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        new_table = TableVisual(
            **{
                **old_table.__dict__,
                "title": "Table 32-7. Receiver limits",
            }
        )
        table_change = TableChange(
            "modified",
            (old_table,),
            (new_table,),
            1.0,
            True,
            (),
        )

        self.assertEqual(1, len(raw_change.replaced_snippets))
        self.assertIsNone(_reader_section_change(raw_change, [table_change]))

    def test_repeated_equal_text_changes_keep_each_raw_occurrence(self) -> None:
        """Two equal-looking edits in one section remain two auditable facts."""

        old_text = (
            "1 Receiver limits\n"
            "The first independent path limit shall be 1 UI.\n"
            "This calibration sentence separates the two requirements.\n"
            "The first independent path limit shall be 1 UI."
        )
        new_text = old_text.replace("shall be 1 UI", "shall be 2 UI")
        result = compare_extractions(
            ExtractionResult(Path("old.pdf"), [PageText(1, old_text)]),
            ExtractionResult(Path("new.pdf"), [PageText(1, new_text)]),
            DiffOptions(),
        )

        pairs = [pair for change in result.changes for pair in change.replaced_snippets]
        matching_pairs = [
            pair
            for pair in pairs
            if "shall be 1 UI" in pair.old and "shall be 2 UI" in pair.new
        ]
        self.assertEqual(2, len(matching_pairs))
        self.assertEqual(0, sum(change.omitted_snippet_count for change in result.changes))

    def test_snippet_display_limit_keeps_complete_json_and_csv_audit(self) -> None:
        """Reader limits never discard the original text of omitted differences."""

        old_text = (
            "1 Receiver limits\n"
            "The alpha jitter limit shall be 1 ps.\n"
            "The unchanged calibration anchor remains active.\n"
            "The beta voltage limit shall be 100 mV.\n"
            "The unchanged routing anchor remains active.\n"
            "The gamma preset shall be P1."
        )
        new_text = (
            old_text.replace("1 ps", "2 ps")
            .replace("100 mV", "200 mV")
            .replace("P1", "P2")
        )
        options = DiffOptions(max_snippets_per_section=1)
        result = compare_extractions(
            ExtractionResult(Path("old.pdf"), [PageText(1, old_text)]),
            ExtractionResult(Path("new.pdf"), [PageText(1, new_text)]),
            options,
        )
        change = result.changes[0]

        self.assertEqual(1, len(change.replaced_snippets))
        self.assertEqual(3, len(change.audit_replaced_snippets or []))
        self.assertEqual(2, change.omitted_snippet_count)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = write_reports(result, temp_dir, options)
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            csv_text = paths["csv"].read_text(encoding="utf-8-sig")

        serialized_changes = json.dumps(payload["changes"], ensure_ascii=False)
        for expected in ("2 ps", "200 mV", "P2"):
            self.assertIn(expected, serialized_changes)
            self.assertIn(expected, csv_text)
        self.assertTrue(payload["changes"][0]["snippet_audit_complete"])

    def test_short_table_number_does_not_match_composite_reference(self) -> None:
        """`Table 1` remains independent from `Table 1-1`, including en dashes."""

        self.assertFalse(_text_contains_table_reference("See Table 1-1 for limits.", "1"))
        self.assertFalse(_text_contains_table_reference("See Table 1–1 for limits.", "1"))
        self.assertTrue(_text_contains_table_reference("See Table 1–1 for limits.", "1-1"))
        for unsupported in (
            "See Table 1A for limits.",
            "See Table 1a for limits.",
            "See Table 1_legacy for limits.",
            "See Table 1.1 for limits.",
            "See Table 1.A for limits.",
            "See Table 1.alpha for limits.",
            "See Table 1/legacy for limits.",
            "See Table 1--1 for limits.",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertFalse(_text_contains_table_reference(unsupported, "1"))
        for unsupported_caption in (
            "Table 1A. Limits",
            "Table 1a. Limits",
            "Table 1_legacy. Limits",
            "Table 1.1. Limits",
            "Table 1.A. Limits",
            "Table 1.alpha. Limits",
            "Table 1/legacy. Limits",
            "Table 1--1. Limits",
        ):
            with self.subTest(unsupported_caption=unsupported_caption):
                self.assertEqual("", _table_caption_number(unsupported_caption))

        old_text = (
            "1 Receiver requirements\n"
            "The composite requirement is specified in Table 1-1 for every receiver.\n"
            "Table 1."
        )
        new_text = (
            "1 Receiver requirements\n"
            "The composite requirement is specified in Table 2-1 for every receiver.\n"
            "Table 2."
        )
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-short-table.pdf"),
                pages=[PageText(page_number=1, text=old_text)],
            ),
            ExtractionResult(
                pdf_path=Path("new-short-table.pdf"),
                pages=[PageText(page_number=1, text=new_text)],
            ),
            DiffOptions(),
        )

        pairs = [pair for change in result.changes for pair in change.replaced_snippets]
        self.assertTrue(any(pair.old.strip() == "Table 1." for pair in pairs))
        self.assertTrue(any("Table 1-1" in pair.old for pair in pairs))

    def test_consecutive_deleted_prose_renders_as_one_coherent_block(self) -> None:
        """Only sentences in one uninterrupted delete opcode become one paragraph."""

        shared = "The receiver shall preserve every common calibration requirement. " * 8
        first = "The first deleted sentence defines the measurement method."
        second = "The second deleted sentence explains the transition set."
        middle = "This unchanged sentence separates two independent revisions."
        ending = "This unchanged ending remains in both protocol revisions."

        def compare_bodies(old_body: str, new_body: str):
            return compare_extractions(
                ExtractionResult(
                    pdf_path=Path("old-delete-runs.pdf"),
                    pages=[PageText(page_number=1, text=f"1 Scope\n{shared}\n{old_body}")],
                ),
                ExtractionResult(
                    pdf_path=Path("new-delete-runs.pdf"),
                    pages=[PageText(page_number=1, text=f"1 Scope\n{shared}\n{new_body}")],
                ),
                DiffOptions(),
            )

        consecutive = compare_bodies(
            f"{first}\n{second}\n{ending}",
            ending,
        )
        separated = compare_bodies(
            f"{first}\n{middle}\n{second}\n{ending}",
            f"{middle}\n{ending}",
        )

        consecutive_removed = consecutive.changes[0].removed_snippets
        separated_removed = separated.changes[0].removed_snippets
        self.assertEqual(1, len(consecutive_removed))
        self.assertIn(f"{first} {second}", consecutive_removed[0])
        self.assertEqual(2, len(separated_removed))
        self.assertEqual(1, _render_single_list("删除片段", consecutive_removed, "del").count("<li>"))
        self.assertEqual(2, _render_single_list("删除片段", separated_removed, "del").count("<li>"))

    def test_table_caption_is_removed_before_paragraph_lines_are_merged(self) -> None:
        """A same-page visual consumes one caption occurrence, not remote prose."""

        table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 32-8. QPRBS9-CEI Pattern Symbols Used for Jitter/EOJ Measurement",
            bbox=(10.0, 10.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[],
            grid_summary="grid",
        )
        caption = table.title
        shared = "The receiver shall preserve every common calibration requirement. " * 8
        old_sentence = "It is acceptable to meet the EOJ requirement with either pattern."
        new_sentence = "It is required to meet the EOJ requirement with either pattern."
        result = compare_extractions(
            ExtractionResult(
                pdf_path=Path("old-caption.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{shared}\n{caption}\n{old_sentence}")],
                table_visuals=[table],
            ),
            ExtractionResult(
                pdf_path=Path("new-caption.pdf"),
                pages=[PageText(page_number=1, text=f"1 Scope\n{shared}\n{caption}\n{new_sentence}")],
                table_visuals=[table],
            ),
            DiffOptions(),
        )

        replaced_text = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )
        self.assertIn(old_sentence, replaced_text)
        self.assertIn(new_sentence, replaced_text)
        self.assertNotIn(caption, replaced_text)

    def test_remote_same_caption_and_table_reference_deletions_remain_visible(self) -> None:
        """Caption suppression is bound to the visual's page and occurrence count."""

        caption = "Table 1. Limits"
        table = TableVisual(
            page_number=1,
            table_number=1,
            title=caption,
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[],
            grid_summary="grid",
        )
        shared = "The receiver shall preserve every common calibration requirement. " * 10
        for remote_line in (caption, "Table 1."):
            with self.subTest(remote_line=remote_line):
                old = ExtractionResult(
                    pdf_path=Path("old-remote-caption.pdf"),
                    pages=[
                        PageText(page_number=1, text=f"1 Scope\n{shared}\n{caption}"),
                        PageText(page_number=2, text=remote_line),
                    ],
                    table_visuals=[table],
                )
                new = ExtractionResult(
                    pdf_path=Path("new-remote-caption.pdf"),
                    pages=[
                        PageText(page_number=1, text=f"1 Scope\n{shared}\n{caption}"),
                        PageText(page_number=2, text=""),
                    ],
                    table_visuals=[table],
                )
                result = compare_extractions(old, new, DiffOptions())
                removed = [
                    snippet
                    for change in result.changes
                    for snippet in change.removed_snippets
                ]
                self.assertIn(remote_line, removed)

    def test_visual_caption_consumes_full_title_or_short_number_but_not_both(self) -> None:
        """An extra same-page `Table N.` occurrence remains a reportable deletion."""

        caption = "Table 1. Limits"
        table = TableVisual(
            page_number=1,
            table_number=1,
            title=caption,
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[],
            grid_summary="grid",
        )
        shared = "The receiver shall preserve every common calibration requirement. " * 10
        old = ExtractionResult(
            pdf_path=Path("old-caption-occurrence.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{shared}\nTable 1.\n{caption}")],
            table_visuals=[table],
        )
        new = ExtractionResult(
            pdf_path=Path("new-caption-occurrence.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{shared}\n{caption}")],
            table_visuals=[table],
        )

        result = compare_extractions(old, new, DiffOptions())
        removed = [
            snippet
            for change in result.changes
            for snippet in change.removed_snippets
        ]

        self.assertIn("Table 1.", removed)

    def test_duplicate_structured_row_on_another_page_gets_its_own_fallback(self) -> None:
        """A visual row on page 1 cannot consume the same row deleted from page 2."""

        row = "表格行: T1 | Parameter=Limit | Symbol=X | Value=1 | Units=dB"
        table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1. Limits",
            bbox=(0.0, 0.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[row],
            grid_summary="grid",
        )
        shared = "The receiver shall preserve every common calibration requirement. " * 10
        old = ExtractionResult(
            pdf_path=Path("old-duplicate-row.pdf"),
            pages=[
                PageText(page_number=1, text=f"1 Scope\n{shared}\n{row}"),
                PageText(page_number=2, text=row),
            ],
            table_visuals=[table],
        )
        new = ExtractionResult(
            pdf_path=Path("new-duplicate-row.pdf"),
            pages=[PageText(page_number=1, text=f"1 Scope\n{shared}\n{row}")],
            table_visuals=[table],
        )

        result = compare_extractions(old, new, DiffOptions())
        table_changes = _build_table_changes(result)

        self.assertTrue(
            any(
                change.change_type == "deleted"
                and any(visual.page_number == 2 for visual in change.old_tables)
                for change in table_changes
            )
        )

    def test_math_only_fragment_is_folded_for_readers_but_never_globally_suppressed(self) -> None:
        """A remote table cannot hide an equal-looking ordinary math line."""

        table = TableVisual(
            page_number=1,
            table_number=1,
            title="Table 1. Thresholds",
            bbox=(10.0, 10.0, 100.0, 100.0),
            image_data_uri="",
            row_texts=[
                "表格行: T1 | Label=R01 | Threshold Level=-1 -1/3",
                "表格行: T1 | Label=R12 | Threshold Level=-1/3 1/3",
            ],
            grid_summary="grid",
        )
        text = "The threshold is defined by the table.\n-1 -1/3 1/3"

        units = _paragraph_review_units(
            text,
            suppressed_table_unit_keys=_covered_table_visual_row_keys([table]),
        )
        self.assertIn("The threshold is defined by the table.", units)
        self.assertIn("-1 -1/3 1/3", units)
        reader_html = _render_single_list("删除片段", ["-1 -1/3 1/3"], "del")
        self.assertIn("疑似表格或公式的版面文字已折叠", reader_html)
        self.assertIn("-1 -1/3 1/3", reader_html)

    def test_continuation_table_inherits_caption_from_previous_page(self) -> None:
        """A caption at a page bottom belongs to the table starting on the next page."""

        continuation = TableVisual(
            page_number=2,
            table_number=1,
            title="",
            bbox=(10.0, 20.0, 100.0, 200.0),
            image_data_uri="",
            row_texts=[],
            grid_summary="grid",
            is_continuation=True,
            page_bbox=(0.0, 0.0, 120.0, 800.0),
        )
        visuals = _table_visuals_with_cross_page_captions(
            [continuation],
            [
                PageText(
                    page_number=1,
                    text=(
                        "The preceding paragraph remains ordinary prose.\n"
                        "Table 32-7. QPRBS13-CEI Pattern Symbols Used for Jitter Measurement"
                    ),
                ),
                PageText(page_number=2, text=""),
            ],
        )

        self.assertEqual(
            "Table 32-7. QPRBS13-CEI Pattern Symbols Used for Jitter Measurement",
            visuals[0].title,
        )

    def test_continuation_caption_ignores_proven_line_numbers_and_footer(self) -> None:
        """A bottom caption may precede extracted line numbers and a running footer."""

        caption_line = (
            "Table 32-7. QPRBS13-CEI Pattern Symbols Used for Jitter Measurement 47"
        )
        page_tail = (
            "The preceding paragraph remains ordinary prose.\n"
            f"{caption_line}\n"
            "48\n"
            "49\n"
            "Optical Internetworking Forum - Clause 32: "
            "CEI-224G-MR-PAM4 Medium Reach Interface 15"
        )

        self.assertEqual(
            "Table 32-7. QPRBS13-CEI Pattern Symbols Used for Jitter Measurement",
            _last_table_caption_line(
                PageText(
                    1,
                    page_tail,
                    blocks=(
                        DocumentBlock(
                            1,
                            (123.0, 675.0, 568.0, 689.0),
                            DocumentBlockKind.TEXT,
                            caption_line,
                            0,
                            "pdfplumber",
                        ),
                        DocumentBlock(
                            1,
                            (555.0, 689.0, 568.0, 701.0),
                            DocumentBlockKind.TEXT,
                            "48",
                            1,
                            "pdfplumber",
                        ),
                    ),
                    page_bbox=(0.0, 0.0, 612.0, 792.0),
                    ambiguous_line_number_sides=("right",),
                )
            ),
        )

    def test_continuation_caption_rejoins_a_title_case_wrapped_tail(self) -> None:
        """A visual title wrap must not turn one unchanged continuation into add/delete."""

        page_tail = (
            "The preceding paragraph remains ordinary prose.\n"
            "Table 32-7. QPRBS13-CEI Pattern Symbols Used\n"
            "for Jitter Measurement\n"
            "48\n"
            "49\n"
            "Optical Internetworking Forum - Clause 32: Interface 15"
        )

        self.assertEqual(
            "Table 32-7. QPRBS13-CEI Pattern Symbols Used for Jitter Measurement",
            _last_table_caption_line(page_tail),
        )

    def test_continuation_caption_rejoins_a_sentence_case_wrapped_tail(self) -> None:
        """A connective line wrap may continue in sentence case without becoming prose."""

        self.assertEqual(
            "Table 1. Requirements for receiver operation",
            _last_table_caption_line(
                "Table 1. Requirements for\n"
                "receiver operation\n"
                "47\n"
                "Optical Internetworking Forum - Clause 1: Interface 15"
            ),
        )

    def test_wrapped_cross_page_caption_is_consumed_and_pairs_the_same_table(self) -> None:
        """Caption wrapping alone produces neither a body delta nor table add/delete."""

        row = "表格行: T1 | Parameter=Limit | Symbol=X | Value=1 | Units=dB"
        continuation = TableVisual(
            page_number=2,
            table_number=1,
            title="",
            bbox=(10.0, 20.0, 100.0, 200.0),
            image_data_uri="",
            row_texts=[row],
            grid_summary="grid",
            is_continuation=True,
            page_bbox=(0.0, 0.0, 120.0, 800.0),
            content_fully_represented=True,
            row_alignment_reliable=True,
            data_rows_fully_represented=True,
        )
        shared = "The receiver shall preserve every calibration requirement. " * 8
        old_extraction = ExtractionResult(
            Path("old.pdf"),
            [
                PageText(
                    1,
                    f"1 Scope\n{shared}\n"
                    "Table 32-7. QPRBS13-CEI Pattern Symbols Used for Jitter Measurement",
                ),
                PageText(2, row),
            ],
            table_visuals=[continuation],
        )
        new_extraction = ExtractionResult(
            Path("new.pdf"),
            [
                PageText(
                    1,
                    f"1 Scope\n{shared}\n"
                    "Table 32-7. QPRBS13-CEI Pattern Symbols Used\n"
                    "for Jitter Measurement",
                ),
                PageText(2, row),
            ],
            table_visuals=[continuation],
        )

        result = compare_extractions(old_extraction, new_extraction, DiffOptions())

        expected_title = (
            "Table 32-7. QPRBS13-CEI Pattern Symbols Used for Jitter Measurement"
        )
        self.assertEqual([expected_title], [table.title for table in result.old_table_visuals])
        self.assertEqual([expected_title], [table.title for table in result.new_table_visuals])
        self.assertEqual([], result.changes)
        self.assertEqual([], reporting_module._build_table_changes(result))

    def test_cross_page_caption_consumes_revision_dependent_line_numbers_and_footer(self) -> None:
        """Page-number drift below an unchanged continuation caption is not a body change."""

        row = "表格行: T1 | Parameter=Limit | Symbol=X | Value=1 | Units=dB"
        continuation = TableVisual(
            page_number=2,
            table_number=1,
            title="",
            bbox=(10.0, 20.0, 100.0, 200.0),
            image_data_uri="",
            row_texts=[row],
            grid_summary="grid",
            is_continuation=True,
            page_bbox=(0.0, 0.0, 120.0, 800.0),
            content_fully_represented=True,
            row_alignment_reliable=True,
            data_rows_fully_represented=True,
        )
        shared = "The receiver shall preserve every calibration requirement. " * 8

        def extraction(name: str, embedded: int, first_line: int, page: int) -> ExtractionResult:
            caption_line = (
                "Table 32-7. QPRBS13-CEI Pattern Symbols Used "
                f"for Jitter Measurement {embedded}"
            )
            return ExtractionResult(
                Path(name),
                [
                    PageText(
                        1,
                        f"1 Scope\n{shared}\n"
                        f"{caption_line}\n"
                        f"{first_line}\n{first_line + 1}\n"
                        "Optical Internetworking Forum - Clause 32: "
                        f"CEI-224G-MR-PAM4 Medium Reach Interface {page}",
                        blocks=(
                            DocumentBlock(
                                1,
                                (123.0, 675.0, 568.0, 689.0),
                                DocumentBlockKind.TEXT,
                                caption_line,
                                0,
                                "pdfplumber",
                            ),
                            DocumentBlock(
                                1,
                                (555.0, 689.0, 568.0, 701.0),
                                DocumentBlockKind.TEXT,
                                str(first_line),
                                1,
                                "pdfplumber",
                            ),
                        ),
                        page_bbox=(0.0, 0.0, 612.0, 792.0),
                        ambiguous_line_number_sides=("right",),
                    ),
                    PageText(2, row),
                ],
                table_visuals=[continuation],
            )

        result = compare_extractions(
            extraction("old.pdf", 47, 48, 15),
            extraction("new.pdf", 46, 47, 14),
            DiffOptions(),
        )

        self.assertEqual([], result.changes)
        self.assertEqual([], reporting_module._build_table_changes(result))

    def test_semantic_numeric_caption_tail_is_never_guessed_as_a_line_number(self) -> None:
        """A technical caption number remains a visible fact without gutter geometry."""

        row = "表格行: T1 | Parameter=Limit | Value=1 | Units=UI"
        continuation = TableVisual(
            page_number=2,
            table_number=1,
            title="",
            bbox=(10.0, 20.0, 100.0, 200.0),
            image_data_uri="",
            row_texts=[row],
            grid_summary="grid",
            is_continuation=True,
            page_bbox=(0.0, 0.0, 120.0, 800.0),
            content_fully_represented=True,
            row_alignment_reliable=True,
            data_rows_fully_represented=True,
        )
        shared = "The receiver shall preserve every calibration requirement. " * 8

        def extraction(name: str, lane: int) -> ExtractionResult:
            return ExtractionResult(
                Path(name),
                [
                    PageText(
                        1,
                        f"1 Scope\n{shared}\nTable 1. Lane {lane}\n"
                        f"{lane + 1}\n{lane + 2}",
                    ),
                    PageText(2, row),
                ],
                table_visuals=[continuation],
            )

        result = compare_extractions(
            extraction("old.pdf", 47),
            extraction("new.pdf", 48),
            DiffOptions(),
        )
        table_changes = reporting_module._build_table_changes(result)

        self.assertEqual("Table 1. Lane 47", result.old_table_visuals[0].title)
        self.assertEqual("Table 1. Lane 48", result.new_table_visuals[0].title)
        self.assertEqual(1, len(table_changes))
        self.assertTrue(table_changes[0].caption_changed)

    def test_continuation_caption_never_scans_past_trailing_prose(self) -> None:
        """Ordinary prose after a caption defeats cross-page title inheritance."""

        self.assertEqual(
            "",
            _last_table_caption_line(
                "Table 32-7. Completed table\n"
                "This sentence discusses a requirement that continues on the next page.\n"
                "47\n"
                "Optical Internetworking Forum - Clause 32: Interface 15"
            ),
        )

        for intervening_prose in (
            "See Clause 32 - receiver overview",
            "https://example.com receiver overview",
        ):
            with self.subTest(intervening_prose=intervening_prose):
                self.assertEqual(
                    "",
                    _last_table_caption_line(
                        "Table 32-7. Completed table\n" + intervening_prose
                    ),
                )

    def test_unnumbered_table_context_keeps_semantic_trailing_numbers(self) -> None:
        """A lane or revision number in an unnumbered title is not a gutter line number."""

        for title in (
            "The following table defines Lane 12",
            "Revision History 3",
        ):
            with self.subTest(title=title):
                self.assertEqual(
                    title,
                    pdf_extract_module._strip_caption_line_noise(title),
                )

    def test_continuation_does_not_inherit_a_caption_far_from_previous_page_end(self) -> None:
        """An earlier completed table title cannot rename a next-page continuation."""

        continuation = TableVisual(
            page_number=2,
            table_number=1,
            title="",
            bbox=(10.0, 20.0, 100.0, 200.0),
            image_data_uri="",
            row_texts=[],
            grid_summary="grid",
            is_continuation=True,
            page_bbox=(0.0, 0.0, 120.0, 800.0),
        )
        for trailing_prose in (
            ["This final sentence is unrelated to any table."],
            [
                "The table above is complete.",
                "This final sentence is unrelated to any table.",
            ],
            [
                "The table above is complete.",
                "A new section begins here.",
                "Its prose continues to the page boundary.",
                "This final sentence is unrelated to any table.",
            ],
        ):
            with self.subTest(trailing_prose=trailing_prose):
                visuals = _table_visuals_with_cross_page_captions(
                    [continuation],
                    [
                        PageText(
                            page_number=1,
                            text="\n".join(
                                ["Table 9-1. Completed historical limits", *trailing_prose]
                            ),
                        ),
                        PageText(page_number=2, text=""),
                    ],
                )
                self.assertEqual("", visuals[0].title)

    def test_cross_reference_number_wrap_rejoins_before_sentence_splitting(self) -> None:
        """`Table 33-\n11.` must produce a complete reference, not `11. This...`."""

        units = _paragraph_review_units(
            (
                "Receiver jitter tolerance is defined in Table 33-\n"
                "11. This sinusoidal jitter is part of the stressed input test."
            ),
            suppressed_table_unit_keys=set(),
        )

        self.assertEqual(
            [
                "Receiver jitter tolerance is defined in Table 33-11.",
                "This sinusoidal jitter is part of the stressed input test.",
            ],
            units,
        )

    def test_first_child_uses_mapped_parent_neighbor_and_descendant_identity(self) -> None:
        """A figure-heavy first child may be paired only with three structural proofs."""

        def section(number: str, title: str, body: str) -> Section:
            parts = number.split(".")
            number_path = tuple(".".join(parts[: index + 1]) for index in range(len(parts)))
            heading = f"{number} {title}"
            return Section(
                section_id=number,
                heading=heading,
                title=title,
                level=len(parts),
                heading_path=(heading,),
                number_path=number_path,
                start_page=1,
                end_page=1,
                body=body,
            )

        stable_root = "The receiver requirements shall define every supported electrical mode."
        stable_parent = "Near-end and far-end channels shall define the module channel model."
        stable_method = "The output test method shall apply the declared calibration sequence."
        stable_sibling = "Input parameters shall preserve the declared receiver tolerance limits."
        old_sections = [
            section("1", "Receiver Requirements", stable_root),
            section("1.7", "Module near-end and far-end channels", stable_parent),
            section(
                "1.7.1",
                "Host and Module output parameters",
                "Legacy diagram alpha beta gamma coordinates and labels. " * 20,
            ),
            section("1.7.1.1", "Host and Module output test method", stable_method),
            section("1.7.2", "Host and Module input parameters", stable_sibling),
        ]
        new_sections = [
            section("1", "Receiver Requirements", stable_root),
            section("1.8", "Module near-end and far-end channels", stable_parent),
            section(
                "1.8.1",
                "Host and Module output parameters",
                "Revised figure voltage current impedance annotations. " * 20,
            ),
            section("1.8.1.1", "Host and Module output test method", stable_method),
            section("1.8.2", "Host and Module input parameters", stable_sibling),
        ]

        matches = _match_sections(
            old_sections,
            new_sections,
            DiffOptions(min_section_match_similarity=0.90),
        )
        target = next(
            match
            for match in matches
            if match[0] is not None
            and old_sections[match[0]].title == "Host and Module output parameters"
        )

        self.assertEqual(2, target[1])
        self.assertEqual("structural_mapped_parent_boundary", target[3])

        without_descendants = _match_sections(
            [section for section in old_sections if section.title != "Host and Module output test method"],
            [section for section in new_sections if section.title != "Host and Module output test method"],
            DiffOptions(min_section_match_similarity=0.90),
        )
        self.assertFalse(
            any(
                old_index is not None
                and new_index is not None
                and old_sections[old_index].title == "Host and Module output parameters"
                for old_index, new_index, _score, _basis in without_descendants
            )
        )


if __name__ == "__main__":
    unittest.main()
