from __future__ import annotations

import csv
from collections import Counter
import hashlib
import io
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from protocol_pdf_diff.compare import (
    _table_visuals_with_text_fallbacks,
    compare_extractions,
    compare_sections,
    run_diff,
)
from protocol_pdf_diff.models import (
    DiffOptions,
    DiffResult,
    DocumentBlock,
    DocumentBlockKind,
    ExtractionResult,
    PageExtractionAudit,
    PageParserRoute,
    PageText,
    Section,
    SectionChange,
    SnippetPair,
    TableChange,
    TableVisual,
    VisualWatchdogAudit,
)
from protocol_pdf_diff.pdf_extract import (
    _deduplicate_nested_captioned_table_visuals,
    _document_physical_page_folio_evidence,
    _physical_page_folio_candidate,
    extract_pdf_text,
)
from protocol_pdf_diff.quality import (
    DiffProvenance,
    EffectiveThresholds,
    InputProvenance,
)
from protocol_pdf_diff.reporting import (
    _build_table_changes,
    _paired_table_visuals,
    _proven_publication_header_section_change,
    _reader_section_change,
    _reader_table_changes,
    _selected_single_page_window_review_pairs,
    _selected_window_folio_page_pairs,
    _selected_window_single_page_visual_bridges,
    _table_change_has_rendered_equal_sources,
    write_reports,
)
from protocol_pdf_diff.sectioning import section_document
from protocol_pdf_diff.visual_watchdog import detect_visual_review_items

_PAGE_BBOX = (0.0, 0.0, 612.0, 792.0)
_PROVEN_FURNITURE_BOXES = (
    (0.0, 0.0, 612.0, 35.0),
    (470.0, 750.0, 612.0, 792.0),
)
_LOCAL_OIF_INPUT_ROOT = (
    Path(__file__).resolve().parents[1] / "work/page-window-gate/input_root/Desktop"
)
_REAL_OIF_CEI_51 = _LOCAL_OIF_INPUT_ROOT / "OIF-CEI-5.1.pdf"
_REAL_OIF_CEI_053 = _LOCAL_OIF_INPUT_ROOT / "OIF-CEI-05.3.pdf"
_REAL_OIF_CEI_40 = _LOCAL_OIF_INPUT_ROOT / "OIF-CEI-04.0.pdf"
_LOCAL_PCIE_INPUT_ROOT = (
    Path(__file__).resolve().parents[1] / "work/page-window-gate/input_root/PCIe"
)
_REAL_PCIE_CEM_11 = _LOCAL_PCIE_INPUT_ROOT / "pci_cem_1_1.pdf"
_REAL_PCIE_CEM_R4 = _LOCAL_PCIE_INPUT_ROOT / "pcie_cem_r4.pdf"
_REAL_PCIE_CEM_R51 = _LOCAL_PCIE_INPUT_ROOT / "pcie_cem_r51.pdf"


def _write_pages(path: Path, masthead: str, folios: list[str], bodies: list[str]) -> str:
    document = fitz.open()
    for folio, body in zip(folios, bodies, strict=True):
        page = document.new_page(width=612, height=792)
        page.insert_text((36, 22), masthead, fontsize=10)
        page.insert_text((54, 112), body, fontsize=12)
        page.insert_text((500, 780), folio, fontsize=9)
    document.save(path)
    document.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_positioned_folio_pages(
    path: Path,
    folios: list[tuple[str | None, str]],
) -> None:
    document = fitz.open()
    for page_number, (folio, side) in enumerate(folios, start=1):
        page = document.new_page(width=612, height=792)
        page.insert_text((54, 112), f"Body clause on page {page_number}.", fontsize=12)
        if folio:
            x = 540 if side == "right" else 18
            page.insert_text((x, 780), folio, fontsize=9)
    document.save(path)
    document.close()


def _write_publication_header_pages(
    path: Path,
    revision: str,
    bodies: list[str],
    *,
    additional_running_header_line: str | None = None,
    banner_prefix: str | None = None,
) -> str:
    document = fitz.open()
    document.set_metadata({"title": "PCI Express Base Specification"})
    version_prefix = banner_prefix or f"{revision}-1.0-PUB"
    for page_number, body in enumerate(bodies, start=1):
        page = document.new_page(width=612, height=792)
        page.insert_text(
            (36, 22),
            f"{version_prefix} - PCI Express Base Specification",
            fontsize=9,
        )
        if additional_running_header_line:
            page.insert_text((36, 48), additional_running_header_line, fontsize=9)
        page.insert_text((54, 112), body, fontsize=12)
        x = 560 if page_number % 2 else 18
        page.insert_text((x, 780), f"Page {page_number}", fontsize=9)
    document.save(path)
    document.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _page_text(
    page_number: int,
    text: str,
    *,
    ocr_used: bool = False,
    image_dominant: bool = False,
) -> PageText:
    blocks = ()
    if not ocr_used:
        blocks = (
            DocumentBlock(
                page_number=page_number,
                bbox=(54.0, 100.0, 500.0, 125.0),
                kind=DocumentBlockKind.TEXT,
                text=text.splitlines()[0],
                reading_order=0,
                source_engine="pdfplumber",
            ),
        )
    return PageText(
        page_number=page_number,
        text=text,
        ocr_used=ocr_used,
        image_dominant=image_dominant,
        blocks=blocks,
        page_bbox=_PAGE_BBOX,
        visual_noise_bboxes=_PROVEN_FURNITURE_BOXES,
        page_identity_noise_bboxes=_PROVEN_FURNITURE_BOXES,
    )


def _extraction(path: Path, digest: str, pages: list[PageText]) -> ExtractionResult:
    return ExtractionResult(
        pdf_path=path,
        pages=pages,
        total_pages=len(pages),
        selected_start_page=pages[0].page_number,
        selected_end_page=pages[-1].page_number,
        source_sha256=digest,
    )


class PageWindowContentCorrespondenceTests(unittest.TestCase):
    def test_short_table_fragment_does_not_hide_an_independent_same_page_value(self) -> None:
        visible = TableVisual(
            page_number=10,
            table_number=1,
            title="Table 1: Receiver Output",
            bbox=(50.0, 100.0, 200.0, 160.0),
            image_data_uri="data:image/jpeg;base64,",
            row_texts=["表格行: T1 | Description=Receiver output level 10 V"],
            grid_summary="visible table",
            source_text="Receiver output level 10 V",
        )
        separate_row = DocumentBlock(
            page_number=10,
            bbox=(250.0, 120.0, 350.0, 140.0),
            kind=DocumentBlockKind.TEXT,
            text="Value=10 V",
            reading_order=1,
            source_engine="pdfplumber",
        )
        page = PageText(
            page_number=10,
            text="表格行: T2 | Value=10 V",
            blocks=(separate_row,),
        )

        result = _table_visuals_with_text_fallbacks([visible], [page])

        self.assertEqual(2, len(result))
        self.assertEqual(["表格行: T2 | Value=10 V"], result[1].row_texts)

    def test_short_fragment_inside_proven_table_source_stays_covered(self) -> None:
        visible = TableVisual(
            page_number=10,
            table_number=1,
            title="Table 1: Receiver Output",
            bbox=(50.0, 100.0, 200.0, 160.0),
            image_data_uri="data:image/jpeg;base64,",
            row_texts=["表格行: T1 | Description=Receiver output level 10 V"],
            grid_summary="visible table",
            source_text="Receiver output level 10 V",
        )
        same_table_row = DocumentBlock(
            page_number=10,
            bbox=(60.0, 110.0, 190.0, 145.0),
            kind=DocumentBlockKind.TEXT,
            text="Value=10 V",
            reading_order=1,
            source_engine="pdfplumber",
        )
        page = PageText(
            page_number=10,
            text="表格行: T2 | Value=10 V",
            blocks=(same_table_row,),
        )

        result = _table_visuals_with_text_fallbacks([visible], [page])

        self.assertEqual([visible], result)

    def test_nested_captioned_table_with_reordered_values_is_preserved(self) -> None:
        parent = TableVisual(
            page_number=52,
            table_number=1,
            title="Table 4-2: Power Supply Rail Requirements",
            bbox=(50.0, 50.0, 550.0, 700.0),
            image_data_uri="data:image/jpeg;base64,",
            row_texts=[
                "表格行: T1 | Parameter=A | Value=1",
                "表格行: T1 | Parameter=B | Value=2",
                "表格行: T1 | Parameter=C | Value=3",
            ],
            grid_summary="parent grid",
        )
        nested = TableVisual(
            page_number=52,
            table_number=2,
            title=parent.title,
            bbox=(100.0, 100.0, 250.0, 200.0),
            image_data_uri="data:image/jpeg;base64,",
            row_texts=[
                "表格行: T2 | Parameter=A | Value=2",
                "表格行: T2 | Parameter=B | Value=1",
            ],
            grid_summary="nested candidate",
        )

        result = _deduplicate_nested_captioned_table_visuals([parent, nested])

        self.assertEqual([parent, nested], result)

    def test_nested_wrapped_header_fragment_still_deduplicates_by_ordered_content(self) -> None:
        parent = TableVisual(
            page_number=52,
            table_number=1,
            title="Table 4-2: Power Supply Rail Requirements",
            bbox=(50.0, 50.0, 550.0, 700.0),
            image_data_uri="data:image/jpeg;base64,",
            row_texts=[
                "表格行: T1 | Column 1=Power\\nRail | Column 2=Connector",
                "表格行: T1 | Column 1=+12V | Column 2=75 W",
                "表格行: T1 | Column 1=+48V | Column 2=600 W",
            ],
            grid_summary="parent grid",
        )
        nested = TableVisual(
            page_number=52,
            table_number=2,
            title=parent.title,
            bbox=(100.0, 100.0, 250.0, 200.0),
            image_data_uri="data:image/jpeg;base64,",
            row_texts=[
                "表格行: T2 | Column 1=Power",
                "表格行: T2 | Column 1=Rail",
            ],
            grid_summary="wrapped header fragment",
        )

        result = _deduplicate_nested_captioned_table_visuals([parent, nested])

        self.assertEqual([parent], result)

    def test_identical_runtime_header_context_is_not_reported_as_same_section(self) -> None:
        def table(page: int, title: str, bbox: tuple[float, float, float, float]) -> TableVisual:
            return TableVisual(
                page_number=page,
                table_number=1,
                title=title,
                bbox=bbox,
                image_data_uri="data:image/jpeg;base64,",
                row_texts=["表格行: T1 | Parameter=Link Width | Value=16"],
                grid_summary="structured source grid",
                page_bbox=_PAGE_BBOX,
            )

        def weak_context_section(section_id: str, page: int) -> Section:
            return Section(
                section_id=section_id,
                heading="运行页眉（坐标证据）",
                title="运行页眉（坐标证据）",
                level=0,
                heading_path=("运行页眉（坐标证据）",),
                number_path=(),
                start_page=page,
                end_page=page,
                body="",
            )

        old = table(
            35,
            "Table 4-1: Power Supply Rail Requirements",
            (210.48, 480.36, 420.78, 631.14),
        )
        new = table(
            42,
            "Table 5: Power Supply Rail Requirements",
            (69.0, 87.36, 601.56, 301.68),
        )

        groups = _paired_table_visuals(
            [old],
            [new],
            old_sections=[weak_context_section("old-header", 35)],
            new_sections=[weak_context_section("new-header", 42)],
        )

        self.assertEqual(1, len(groups))
        self.assertEqual("descriptive-caption-weak-context", groups[0].review_reason)
        self.assertTrue(groups[0].review_only)

    def test_unmapped_private_glyph_on_figure_page_is_review_not_confirmed_text(self) -> None:
        """Formula/diagram glyph mapping on a Figure page cannot become a confirmed prose edit."""

        old_text = "Signal line p_average = p\uf0f2 jitter"
        new_text = "Signal line p_average = p jitter"
        change = SectionChange(
            change_type="modified",
            old_section=None,
            new_section=None,
            similarity=0.9,
            replaced_snippets=[SnippetPair(old_text, new_text)],
            audit_replaced_snippets=[SnippetPair(old_text, new_text)],
        )

        reader_change = _reader_section_change(
            change,
            figure_visual_sides=(True, True),
        )

        self.assertIsNotNone(reader_change)
        self.assertEqual("review", reader_change.change_type)
        self.assertIn("未映射的私用字形", reader_change.review_reason)

    def test_unpaired_selected_window_prelude_is_review_not_confirmed_addition(self) -> None:
        """Text before a selected-window heading lacks an opposite-side absence proof."""

        body = (
            "The receiver shall meet the pre-FEC BER requirement with channel values "
            "defined in Table 32-8."
        )
        prelude = Section(
            section_id="P0021",
            heading="范围起始页前序内容",
            title="范围起始页前序内容",
            level=0,
            heading_path=("范围起始页前序内容",),
            number_path=(),
            start_page=21,
            end_page=21,
            body=body,
        )
        change = SectionChange(
            change_type="added",
            old_section=None,
            new_section=prelude,
            similarity=0.0,
            added_snippets=[body],
        )

        reader_change = _reader_section_change(change)

        self.assertIsNotNone(reader_change)
        self.assertEqual("review", reader_change.change_type)
        self.assertIn("双侧章节/段落身份对应", reader_change.review_reason)
        self.assertTrue(any("pre-FEC BER" in value for value in reader_change.added_snippets))

        old_extraction = ExtractionResult(
            pdf_path=Path("old-window.pdf"),
            pages=[_page_text(21, "Prior section ending outside the selected boundary.")],
            total_pages=200,
            selected_start_page=21,
            selected_end_page=21,
        )
        new_extraction = ExtractionResult(
            pdf_path=Path("new-window.pdf"),
            pages=[_page_text(20, body)],
            total_pages=200,
            selected_start_page=20,
            selected_end_page=20,
        )
        raw_changes = compare_sections(
            [],
            [prelude],
            DiffOptions(),
            source_extractions=(old_extraction, new_extraction),
        )
        self.assertEqual(["review"], [change.change_type for change in raw_changes])
        self.assertTrue(raw_changes[0].review_reason)

    def test_flat_to_nested_table_numbering_keeps_unique_same_caption_pairs_reviewable(self) -> None:
        """A renumbering across numbering schemes is not a whole-table delete/add."""

        def table(page: int, number: int, title: str, bbox, rows: list[str]) -> TableVisual:
            return TableVisual(
                page_number=page,
                table_number=number,
                title=title,
                bbox=bbox,
                image_data_uri="",
                row_texts=rows,
                grid_summary="structured source grid",
                page_bbox=_PAGE_BBOX,
                content_fully_represented=False,
                row_alignment_reliable=True,
                data_rows_fully_represented=False,
            )

        old_tables = [
            table(
                111,
                1,
                "Table 46: Mechanical Test Procedures and Requirements",
                (83.4, 150.4, 575.4, 373.2),
                [
                    "表格行: T1 | Test Description=Insertion force | Procedure=Measure the force | Requirement=1.15 N maximum",
                    "表格行: T1 | Test Description=Visual dimensions | Procedure=EIA 364-18 | Requirement=per drawing",
                ],
            ),
            table(
                111,
                2,
                "Table 47: End of Life Current Rating Test Sequence",
                (83.4, 454.3, 597.8, 663.6),
                [
                    "表格行: T2 | Test=Contact current rating | Procedure=Wire the eight power pins and eight nearest ground pins | Condition=Mated | Requirement=1.1 A per pin",
                    "表格行: T2 | Test=Temperature rise | Procedure=Conduct a temperature rise test | Condition=25 C | Requirement=listed",
                ],
            ),
        ]
        new_tables = [
            table(
                134,
                1,
                "Table 6-10: Mechanical Test Procedures and Requirements",
                (83.4, 143.8, 575.6, 366.6),
                [
                    "表格行: T1 | Test Description=Insertion force | Procedure=Measure the force | Requirement=1.15 N maximum",
                    "表格行: T1 | Test Description=Visual dimensions | Procedure=EIA 364-18 | Requirement=per drawing",
                ],
            ),
            table(
                134,
                2,
                "Table 6-11: End of Life Current Rating Test Sequence",
                (83.4, 453.7, 598.2, 663.1),
                [
                    "表格行: T2 | Test=Contact current rating | Procedure=Wire the nine power pins and nine nearest ground pins | Condition=Mated | Requirement=1.1 A per pin",
                    "表格行: T2 | Test=Temperature rise | Procedure=Conduct a temperature rise test | Condition=25 C | Requirement=listed",
                ],
            ),
        ]

        groups = _paired_table_visuals(old_tables, new_tables)

        self.assertEqual(2, len(groups))
        self.assertTrue(all(group.old_tables and group.new_tables for group in groups))
        self.assertTrue(all(group.review_only for group in groups))
        self.assertTrue(all(group.review_reason == "same-position-content-change" for group in groups))
        self.assertEqual(
            {"Table 46: Mechanical Test Procedures and Requirements", "Table 47: End of Life Current Rating Test Sequence"},
            {group.old_tables[0].title for group in groups},
        )
        self.assertEqual(
            {"Table 6-10: Mechanical Test Procedures and Requirements", "Table 6-11: End of Life Current Rating Test Sequence"},
            {group.new_tables[0].title for group in groups},
        )
        self.assertTrue(any("eight power pins" in row for group in groups for table in group.old_tables for row in table.row_texts))
        self.assertTrue(any("nine power pins" in row for group in groups for table in group.new_tables for row in table.row_texts))

    def test_unique_caption_same_section_table_expansion_stays_review_only_without_bbox_overlap(self) -> None:
        """An expanded table with a unique caption in the same section cannot become delete/add when it moves."""

        def table(page: int, title: str, bbox, rows: list[str]) -> TableVisual:
            return TableVisual(
                page_number=page,
                table_number=1,
                title=title,
                bbox=bbox,
                image_data_uri="data:image/jpeg;base64,",
                row_texts=rows,
                grid_summary="structured source grid",
                page_bbox=_PAGE_BBOX,
                content_fully_represented=False,
                row_alignment_reliable=False,
                data_rows_fully_represented=False,
            )

        old = table(
            35,
            "Table 4-1: Power Supply Rail Requirements",
            (210.48, 480.36, 420.78, 631.14),
            [
                "表格行: T1 | Power Rail=+3.3V | 10 W Slot=±9% / 3.0 A | 25 W Slot=±9% / 3.0 A",
                "表格行: T1 | Power Rail=+12V | 10 W Slot=±8% / 0.5 A | 25 W Slot=±8% / 2.1 A",
            ],
        )
        new = table(
            42,
            "Table 5: Power Supply Rail Requirements",
            (69.0, 87.36, 601.56, 301.68),
            [
                "表格行: T1 | Power Rail=+3.3V | 10 W Slot=±9% / 3.0 A | 25 W Slot=±9% / 3.0 A | 2 x 3 Connector=N/A",
                "表格行: T1 | Power Rail=+12V | 10 W Slot=±8% / 0.5 A | 25 W Slot=±8% / 2.1 A | 2 x 3 Connector=+5%/-8%",
            ],
        )
        old_section = Section(
            section_id="old-4-1",
            heading="4.1. Power Supply Requirements",
            title="Power Supply Requirements",
            level=2,
            heading_path=("4. Electrical Requirements", "4.1. Power Supply Requirements"),
            number_path=("4", "4.1"),
            start_page=35,
            end_page=35,
            body="Power delivery requirements are listed in the Power Supply Rail Requirements table.",
        )
        new_section = replace(
            old_section,
            section_id="new-4-1",
            start_page=42,
            end_page=42,
        )

        groups = _paired_table_visuals(
            [old],
            [new],
            old_sections=[old_section],
            new_sections=[new_section],
        )

        self.assertEqual(1, len(groups))
        self.assertTrue(groups[0].review_only)
        self.assertEqual("descriptive-caption-same-section", groups[0].review_reason)
        self.assertEqual(old, groups[0].old_tables[0])
        self.assertEqual(new, groups[0].new_tables[0])

        conflicting_section = replace(
            new_section,
            heading="4.2. Power Consumption",
            title="Power Consumption",
            heading_path=("4. Electrical Requirements", "4.2. Power Consumption"),
            number_path=("4", "4.2"),
        )
        unpaired = _paired_table_visuals(
            [old],
            [new],
            old_sections=[old_section],
            new_sections=[conflicting_section],
        )
        self.assertEqual(2, len(unpaired))
        self.assertFalse(any(group.review_only for group in unpaired))

    @unittest.skipUnless(
        _REAL_PCIE_CEM_11.is_file() and _REAL_PCIE_CEM_R4.is_file(),
        "本机 PCIe CEM 1.1/R4 样本不存在",
    )
    def test_real_cem_expanded_power_table_is_one_review_candidate(self) -> None:
        """The same uniquely captioned CEM power table must not become whole-table delete/add."""

        options = DiffOptions(
            old_start_page=35,
            old_end_page=36,
            new_start_page=42,
            new_end_page=42,
        )
        result = run_diff(_REAL_PCIE_CEM_11, _REAL_PCIE_CEM_R4, options)
        with tempfile.TemporaryDirectory() as temp_dir:
            reports = write_reports(result, Path(temp_dir) / "reports", options)
            payload = json.loads(reports["json"].read_text(encoding="utf-8"))
            html = reports["html"].read_text(encoding="utf-8")

        table_findings = [
            *payload["content_table_changes"],
            *payload["similarity_review_table_changes"],
        ]
        expanded_table = [
            change
            for change in table_findings
            if any("Table 4-1" in title for title in change["old_titles"])
            and any("Table 5:" in title for title in change["new_titles"])
        ]
        self.assertEqual(1, len(expanded_table), table_findings)
        self.assertEqual("review", expanded_table[0]["change_type"])
        self.assertEqual([35, 36], expanded_table[0]["old_pages"])
        self.assertEqual([42], expanded_table[0]["new_pages"])
        self.assertIn("同描述表题候选复核", json.dumps(expanded_table, ensure_ascii=False))
        self.assertIn("Table 4-1", html)
        self.assertIn("Table 5:", html)
        self.assertGreaterEqual(html.count("<img"), 3)

        with fitz.open(_REAL_PCIE_CEM_11) as old_doc, fitz.open(_REAL_PCIE_CEM_R4) as new_doc:
            old_source = " ".join(old_doc[34].get_text("text").split())
            new_source = " ".join(new_doc[41].get_text("text").split())
        self.assertIn("Table 4-1: Power Supply Rail Requirements", old_source)
        self.assertIn("Table 5: Power Supply Rail Requirements", new_source)
        self.assertIn("2 x 3 Connector", new_source)

    @unittest.skipUnless(
        _REAL_PCIE_CEM_11.is_file() and _REAL_PCIE_CEM_R4.is_file(),
        "本机 PCIe CEM 1.1/R4 样本不存在",
    )
    def test_real_cem_table_to_prose_conversion_keeps_power_changes_visible(self) -> None:
        """Removing a table is acceptable only when its rewritten prose remains in the body diff."""

        options = DiffOptions(
            old_start_page=35,
            old_end_page=37,
            new_start_page=41,
            new_end_page=44,
        )
        result = run_diff(_REAL_PCIE_CEM_11, _REAL_PCIE_CEM_R4, options)
        with tempfile.TemporaryDirectory() as temp_dir:
            reports = write_reports(result, Path(temp_dir) / "reports", options)
            payload = json.loads(reports["json"].read_text(encoding="utf-8"))

        deleted_table = [
            change
            for change in payload["content_table_changes"]
            if change["change_type"] == "deleted"
            and any("Table 4-2: Add-in Card Power Dissipation" in title for title in change["old_titles"])
        ]
        self.assertEqual(1, len(deleted_table), payload["content_table_changes"])
        self.assertEqual([36], deleted_table[0]["old_pages"])

        prose_rewrite = [
            change
            for change in payload["content_changes"]
            if change["change_type"] == "modified"
            and change["old_pages"] == "36"
            and change["new_pages"] == "43-44"
        ]
        self.assertEqual(1, len(prose_rewrite), payload["content_changes"])
        rewrite_evidence = json.dumps(prose_rewrite[0], ensure_ascii=False).casefold()
        for anchor in (
            "x1 low profile card",
            "25 w maximum power dissipation",
            "75 w maximum power dissipation",
        ):
            self.assertIn(anchor, rewrite_evidence)
        old_table_context = json.dumps(
            prose_rewrite[0]["display_removed_snippets"], ensure_ascii=False
        ).casefold()
        self.assertIn("standard height", old_table_context)
        self.assertIn("10 w", old_table_context)

        with fitz.open(_REAL_PCIE_CEM_11) as old_doc, fitz.open(_REAL_PCIE_CEM_R4) as new_doc:
            old_source = " ".join(old_doc[35].get_text("text").split())
            new_source = " ".join(new_doc[42].get_text("text").split())
        self.assertIn("Table 4-2: Add-in Card Power Dissipation", old_source)
        self.assertIn("x1 low profile card", new_source)
        self.assertIn("25 W maximum power dissipation", new_source)
        self.assertIn("75 W maximum power dissipation", new_source)

    @unittest.skipUnless(
        _REAL_PCIE_CEM_R4.is_file(),
        "本机 PCIe CEM R4 样本不存在",
    )
    def test_real_cem_implementation_note_boxes_remain_prose(self) -> None:
        """Word callout boxes labelled IMPLEMENTATION NOTE must not be added as tables."""

        extraction = extract_pdf_text(_REAL_PCIE_CEM_R4, start_page=43, end_page=44)
        note_pages = {43, 44}
        self.assertFalse(
            any(
                table.page_number in note_pages and not table.title.strip()
                for table in extraction.table_visuals
            ),
            [
                (table.page_number, table.title, table.row_texts[:2])
                for table in extraction.table_visuals
            ],
        )
        self.assertTrue(
            any(
                table.page_number == 43 and table.title.startswith("Table 6:")
                for table in extraction.table_visuals
            )
        )
        extracted_text = " ".join(page.text for page in extraction.pages)
        self.assertIn("IMPLEMENTATION NOTE", extracted_text)
        self.assertIn("The 75 W slot requirements are defined in this specification", extracted_text)
        self.assertIn("Power, Thermal Mechanical, and Labeling Considerations", extracted_text)

    @unittest.skipUnless(
        _REAL_PCIE_CEM_R4.is_file() and _REAL_PCIE_CEM_R51.is_file(),
        "本机 PCIe CEM R4/R5.1 样本不存在",
    )
    def test_real_cem_power_table_split_is_one_review_group_without_nested_fragments(self) -> None:
        """A new revision splits power tables; duplicate nested header fragments must not inflate additions."""

        new_extraction = extract_pdf_text(_REAL_PCIE_CEM_R51, start_page=51, end_page=52)
        titled_tables = [table for table in new_extraction.table_visuals if table.title.strip()]
        self.assertEqual(
            [
                "Table 4-1: Power Supply Rail Requirements- PCI Express CEM Connector / Edge-Finger",
                "Table 4-2: Power Supply Rail Requirements - Auxiliary Power Connectors",
            ],
            [table.title for table in titled_tables],
        )

        options = DiffOptions(
            old_start_page=42,
            old_end_page=43,
            new_start_page=51,
            new_end_page=52,
        )
        result = run_diff(_REAL_PCIE_CEM_R4, _REAL_PCIE_CEM_R51, options)
        with tempfile.TemporaryDirectory() as temp_dir:
            reports = write_reports(result, Path(temp_dir) / "reports", options)
            payload = json.loads(reports["json"].read_text(encoding="utf-8"))

        new_visuals = [
            table
            for table in payload["new_table_visuals"]
            if table["page_number"] in {51, 52}
        ]
        self.assertEqual(2, len(new_visuals), new_visuals)

        findings = [
            *payload["content_table_changes"],
            *payload["similarity_review_table_changes"],
        ]
        split_candidate = [
            change
            for change in findings
            if {"Table 5: Power Supply Rail Requirements", "Table 6: 150 W / 225 W / 300 W Power Supply Rail Requirements"}
            <= set(change["old_titles"])
            and {
                "Table 4-1: Power Supply Rail Requirements- PCI Express CEM Connector / Edge-Finger",
                "Table 4-2: Power Supply Rail Requirements - Auxiliary Power Connectors",
            }
            <= set(change["new_titles"])
        ]
        self.assertEqual(1, len(split_candidate), findings)
        self.assertEqual("review", split_candidate[0]["change_type"])
        self.assertEqual([42, 43], split_candidate[0]["old_pages"])
        self.assertEqual([51, 52], split_candidate[0]["new_pages"])

        with fitz.open(_REAL_PCIE_CEM_R4) as old_doc, fitz.open(_REAL_PCIE_CEM_R51) as new_doc:
            old_source = " ".join(
                " ".join(old_doc[page].get_text("text").split())
                for page in (41, 42)
            )
            new_source = " ".join(
                " ".join(new_doc[page].get_text("text").split())
                for page in (50, 51)
            )
        for anchor in (
            "Table 5: Power Supply Rail Requirements",
            "Table 6: 150 W / 225 W / 300 W Power Supply Rail Requirements",
        ):
            self.assertIn(anchor, old_source)
        for anchor in (
            "Table 4-1: Power Supply Rail Requirements",
            "Table 4-2: Power Supply Rail Requirements - Auxiliary Power Connectors",
            "12V-2x6 Connector",
        ):
            self.assertIn(anchor, new_source)

    def test_multipart_caption_core_does_not_override_conflicting_section_identity(self) -> None:
        """A repeated caption core cannot merge tables when all strong section contexts conflict."""

        def table(page: int, number: int, title: str, value: str) -> TableVisual:
            return TableVisual(
                page_number=page,
                table_number=number,
                title=title,
                bbox=(72.0, 100.0, 540.0, 300.0),
                image_data_uri="data:image/jpeg;base64,",
                grid_summary="structured source grid",
                row_texts=[f"表格行: T{number} | Parameter=Unique Value | Value={value} | Unit=W"],
                page_bbox=_PAGE_BBOX,
                content_fully_represented=False,
                row_alignment_reliable=False,
                data_rows_fully_represented=False,
            )

        def section(section_id: str, heading: str, number_path: tuple[str, ...], page: int) -> Section:
            return Section(
                section_id=section_id,
                heading=heading,
                title=heading,
                level=2,
                heading_path=number_path,
                number_path=number_path,
                start_page=page,
                end_page=page,
                body=f"Independent section {heading}.",
            )

        old_sections = [
            section("old-4-1", "4.1 Slot Power", ("4", "4.1"), 35),
            section("old-4-2", "4.2 Auxiliary Power", ("4", "4.2"), 36),
        ]
        new_sections = [
            section("new-5-1", "5.1 Slot Power", ("5", "5.1"), 51),
            section("new-5-2", "5.2 Auxiliary Power", ("5", "5.2"), 52),
        ]
        old_tables = [
            table(35, 1, "Table 5: Power Supply Rail Requirements - legacy slot", "old-slot-17"),
            table(36, 1, "Table 6: 150 W / 225 W / 300 W Power Supply Rail Requirements - legacy auxiliary", "old-aux-29"),
        ]
        new_tables = [
            table(51, 1, "Table 4-1: Power Supply Rail Requirements - revised edge finger", "new-edge-31"),
            table(52, 1, "Table 4-2: Power Supply Rail Requirements - revised connectors", "new-connectors-43"),
        ]

        groups = _paired_table_visuals(
            old_tables,
            new_tables,
            old_sections=old_sections,
            new_sections=new_sections,
        )

        self.assertEqual(4, len(groups))
        self.assertFalse(any(group.review_only for group in groups))
        self.assertTrue(all(bool(group.old_tables) != bool(group.new_tables) for group in groups))

    def test_flat_to_nested_duplicate_caption_is_not_paired_by_position(self) -> None:
        """A repeated description cannot disambiguate which flat table was renumbered."""

        def table(page: int, number: int, title: str, value: str, bbox) -> TableVisual:
            return TableVisual(
                page_number=page,
                table_number=number,
                title=title,
                bbox=bbox,
                image_data_uri="",
                row_texts=[f"表格行: T{number} | Parameter=Shared Limit | Value={value} | Unit=A"],
                grid_summary="structured source grid",
                page_bbox=_PAGE_BBOX,
            )

        old_tables = [
            table(10, 1, "Table 46: End of Life Current Rating Test Sequence", "8 pins", (80, 100, 570, 300)),
            table(12, 1, "Table 47: End of Life Current Rating Test Sequence", "9 pins", (80, 100, 570, 300)),
        ]
        new_tables = [
            table(20, 1, "Table 6-11: End of Life Current Rating Test Sequence", "10 pins", (80, 100, 570, 300)),
        ]

        groups = _paired_table_visuals(old_tables, new_tables)

        self.assertEqual(3, len(groups))
        self.assertTrue(
            all(bool(group.old_tables) != bool(group.new_tables) for group in groups),
            "duplicate descriptive captions must remain one-sided until a stronger anchor exists",
        )

    def test_short_window_publication_footer_is_excluded_from_body_and_kept_as_metadata(self) -> None:
        """A one-page footer signature stays auditable without becoming body prose."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = (root / "phy-old.pdf", root / "phy-new.pdf")
            for path, revision, date in zip(
                paths,
                ("6.0", "6.x"),
                ("April 22, 2026", "June 4, 2026"),
                strict=True,
            ):
                document = fitz.open()
                for page_number in (1, 2):
                    page = document.new_page(width=612, height=792)
                    page.insert_text((54, 120), "1. Test requirement", fontsize=12)
                    page.insert_text((54, 150), "The transmitter shall preserve the specified limit.", fontsize=12)
                    page.insert_text(
                        (72, 692),
                        f"PCI Express Architecture PHY Test Specification | {page_number}",
                        fontsize=9,
                    )
                    page.insert_text((72, 705), f"Revision {revision}", fontsize=9)
                    page.insert_text((72, 718), date, fontsize=9)
                document.save(path)
                document.close()
            body_only_path = root / "phy-body-revision.pdf"
            body_only = fitz.open()
            body_page = body_only.new_page(width=612, height=792)
            body_page.insert_text((54, 300), "Revision 6.0", fontsize=12)
            body_page.insert_text((54, 330), "This revision identifier is in the body.", fontsize=12)
            body_only.save(body_only_path)
            body_only.close()

            old = extract_pdf_text(paths[0], 1, 2)
            new = extract_pdf_text(paths[1], 1, 2)
            body_only_result = extract_pdf_text(body_only_path, 1, 1)
            old_footer = " ".join(value for page in old.pages for value in page.running_footer_texts)
            new_footer = " ".join(value for page in new.pages for value in page.running_footer_texts)

        self.assertNotIn("Revision 6.0", "\n".join(page.text for page in old.pages))
        self.assertNotIn("Revision 6.x", "\n".join(page.text for page in new.pages))
        self.assertIn("Revision 6.0", old_footer)
        self.assertIn("April 22, 2026", old_footer)
        self.assertIn("Revision 6.x", new_footer)
        self.assertIn("June 4, 2026", new_footer)
        self.assertTrue(all(page.page_identity_noise_bboxes for page in (*old.pages, *new.pages)))
        self.assertIn("Revision 6.0", body_only_result.pages[0].text)
        self.assertFalse(body_only_result.pages[0].running_footer_texts)

    def test_forum_clause_footer_filter_preserves_bottom_technical_limit(self) -> None:
        """Bottom Forum/Clause sentences retain voltage and SerDes baud deltas."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = (
                ("voltage", "Maximum receiver input is", "4.2 V", "4.8 V"),
                ("baud", "CEI supports", "112 GBd", "116 GBd"),
            )
            for case_name, prefix, old_value, new_value in cases:
                case_root = root / case_name
                case_root.mkdir()
                paths = (case_root / "old.pdf", case_root / "new.pdf")
                for path, value in zip(paths, (old_value, new_value), strict=True):
                    document = fitz.open()
                    page = document.new_page(width=612, height=792)
                    page.insert_text((54, 120), "11 Technical Values", fontsize=12)
                    page.insert_text((54, 150), "The stated value is checked at the receiver.", fontsize=10)
                    page.insert_text(
                        (72, 725),
                        f"Optical Internetworking Forum Clause 11: {prefix} {value}",
                        fontsize=8,
                    )
                    # A separated edge number resembles a footer folio. It must not
                    # grant deletion authority to a line that contains a technical value.
                    page.insert_text((540, 725), "1", fontsize=8)
                    document.save(path)
                    document.close()

                extracted = [extract_pdf_text(path, 1, 1) for path in paths]
                options = DiffOptions(
                    old_start_page=1,
                    old_end_page=1,
                    new_start_page=1,
                    new_end_page=1,
                )
                result = run_diff(paths[0], paths[1], options)
                reports = write_reports(result, case_root / "reports", options)
                payload = json.loads(reports["json"].read_text(encoding="utf-8"))
                reader_changes = json.dumps(payload["content_changes"], ensure_ascii=False)

                self.assertIn(old_value, extracted[0].pages[0].text)
                self.assertIn(new_value, extracted[1].pages[0].text)
                self.assertIn(old_value, reader_changes)
                self.assertIn(new_value, reader_changes)
                self.assertTrue(
                    any(change["change_type"] == "modified" for change in payload["content_changes"])
                )

    def test_forum_clause_url_footer_cluster_preserves_technical_values(self) -> None:
        """A nearby publication URL must not authorize removing a technical value."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cases = (
                (
                    "value_on_marker_line",
                    lambda page, value: (
                        page.insert_text(
                            (72, 725),
                            f"Optical Internetworking Forum - Clause 11: Maximum receiver input is {value}",
                            fontsize=8,
                        ),
                        page.insert_text((72, 742), "www.oiforum.com", fontsize=8),
                    ),
                    "4.2 V",
                    "4.8 V",
                ),
                (
                    "value_inside_footer_cluster",
                    lambda page, value: (
                        page.insert_text(
                            (72, 705),
                            "Optical Internetworking Forum - Clause 11: CEI-25G-LR Interface",
                            fontsize=8,
                        ),
                        page.insert_text(
                            (72, 720),
                            f"Maximum receiver input is {value}",
                            fontsize=8,
                        ),
                        page.insert_text((72, 735), "www.oiforum.com", fontsize=8),
                    ),
                    "4.2 V",
                    "4.8 V",
                ),
                (
                    "normative_body_inside_legal_footer_cluster",
                    lambda page, modal: (
                        page.insert_text(
                            (72, 705),
                            "Optical Internetworking Forum - Clause 32: Link Requirements",
                            fontsize=8,
                        ),
                        page.insert_text(
                            (72, 720),
                            f"The receiver {modal} support the declared operating mode.",
                            fontsize=8,
                        ),
                        page.insert_text(
                            (72, 735),
                            "Copyright © 2025 Optical Internetworking Forum",
                            fontsize=8,
                        ),
                        page.insert_text(
                            (72, 750),
                            "This is a draft and not to be shared before publication approval.",
                            fontsize=8,
                        ),
                    ),
                    "shall",
                    "may",
                ),
            )
            for case_name, draw_footer, old_value, new_value in cases:
                case_root = root / case_name
                case_root.mkdir()
                paths = (case_root / "old.pdf", case_root / "new.pdf")
                values = (old_value, new_value)
                for path, value in zip(paths, values, strict=True):
                    document = fitz.open()
                    page = document.new_page(width=612, height=792)
                    page.insert_text((54, 120), "11 Technical Values", fontsize=12)
                    page.insert_text((54, 150), "The stated value is checked at the receiver.", fontsize=10)
                    draw_footer(page, value)
                    document.save(path)
                    document.close()

                extracted = [extract_pdf_text(path, 1, 1) for path in paths]
                options = DiffOptions(
                    old_start_page=1,
                    old_end_page=1,
                    new_start_page=1,
                    new_end_page=1,
                )
                result = run_diff(paths[0], paths[1], options)
                reports = write_reports(result, case_root / "reports", options)
                payload = json.loads(reports["json"].read_text(encoding="utf-8"))
                reader_changes = json.dumps(payload["content_changes"], ensure_ascii=False)

                self.assertIn(values[0], extracted[0].pages[0].text)
                self.assertIn(values[1], extracted[1].pages[0].text)
                self.assertIn(values[0], reader_changes)
                self.assertIn(values[1], reader_changes)
                self.assertTrue(
                    any(change["change_type"] == "modified" for change in payload["content_changes"])
                )

    def test_open_vector_curve_preserves_unstructured_table_in_public_comparison(self) -> None:
        """A curve bbox cannot make a real one-row technical grid disappear."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = (root / "old.pdf", root / "new.pdf")
            values = ("35 ps", "40 ps")
            for path, value in zip(paths, values, strict=True):
                document = fitz.open()
                page = document.new_page(width=612, height=792)
                page.insert_text((170, 140), "Figure 1. Example response curve", fontsize=12)
                page.draw_bezier(
                    (80, 180),
                    (540, 180),
                    (80, 580),
                    (540, 580),
                    color=(0, 0, 0),
                    width=1,
                )  # 大型开放曲线覆盖整幅图，但不构成闭合框。
                for y in (320, 370):
                    page.draw_line((150, y), (390, y), color=(0, 0, 0), width=1)
                for x in (150, 270, 390):
                    page.draw_line((x, 320), (x, 370), color=(0, 0, 0), width=1)
                for index in range(5):
                    x = 90 + index * 25
                    page.draw_line(
                        (x, 220),
                        (x + 10, 223),
                        color=(0, 0, 0),
                        width=1,
                    )  # 使表格候选通过页面几何预筛，但不提供闭合框证据。
                page.insert_text((158, 342), "Rise Time", fontsize=10)
                page.insert_text((280, 342), value, fontsize=10)
                document.save(path)
                document.close()

            extracted = [extract_pdf_text(path, 1, 1) for path in paths]
            for extraction, value in zip(extracted, values, strict=True):
                self.assertEqual(1, len(extraction.table_visuals))
                self.assertIn(value, " ".join(extraction.table_visuals[0].row_texts))

            options = DiffOptions(
                old_start_page=1,
                old_end_page=1,
                new_start_page=1,
                new_end_page=1,
            )
            result = run_diff(paths[0], paths[1], options)
            reports = write_reports(result, root / "reports", options)
            payload = json.loads(reports["json"].read_text(encoding="utf-8"))
            html = reports["html"].read_text(encoding="utf-8")
            reader_table_findings = [
                *payload["content_table_changes"],
                *payload["similarity_review_table_changes"],
            ]
            reader_table_changes = json.dumps(reader_table_findings, ensure_ascii=False)
            source_table_rows = json.dumps(
                [*payload["old_table_visuals"], *payload["new_table_visuals"]],
                ensure_ascii=False,
            )

            self.assertIn("Rise Time", source_table_rows)
            self.assertIn(values[0], reader_table_changes)
            self.assertIn(values[1], reader_table_changes)
            self.assertIn("similarity-review-appendix", html)
            self.assertIn(values[0], html)
            self.assertIn(values[1], html)
            self.assertEqual(
                ["review"],
                [change["change_type"] for change in reader_table_findings],
                "同一单页中同框、同一唯一参数标签的无题表格不能误报为整表删除和新增",
            )
            row_changes = reader_table_findings[0]["row_changes"]
            self.assertTrue(
                any(
                    values[0] in row["old_value"]
                    and values[1] in row["new_value"]
                    and row["change_type"] == "需人工复核"
                    for row in row_changes
                ),
                row_changes,
            )

    def test_single_page_line_number_style_change_keeps_the_actual_value_delta(self) -> None:
        """A left/right gray print-line gutter must not become technical prose."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = (root / "left-gutter.pdf", root / "right-gutter.pdf")
            blank_rows = {2, 7, 12, 17, 22, 27, 32, 37, 42, 47}

            def write_numbered_page(path: Path, side: str, value: str) -> None:
                document = fitz.open()
                page = document.new_page(width=612, height=792)
                for line_number in range(1, 50):
                    baseline = 92 + (line_number - 1) * 12.4
                    number_text = str(line_number)
                    if side == "left":
                        x = 61 - fitz.get_text_length(number_text, fontname="helv", fontsize=8)
                    else:
                        x = 552
                    page.insert_text(
                        (x, baseline),
                        number_text,
                        fontsize=8,
                        color=(0.6, 0.6, 0.6),
                    )
                    if line_number in blank_rows:
                        continue
                    if line_number == 1:
                        text = "2.9.4 Transmitter Signal Quality Test"
                    elif line_number == 18:
                        text = f"The receiver input limit is {value}."
                    else:
                        text = f"The test procedure records a stable waveform at step {line_number}."
                    page.insert_text((72, baseline), text, fontsize=8)
                page.insert_text(
                    (72, 742),
                    "Optical Internetworking Forum - Clause 33: CEI Test Interface",
                    fontsize=7,
                )
                page.insert_text((550, 742), "1", fontsize=7)
                document.save(path)
                document.close()

            write_numbered_page(paths[0], "left", "4.2 V")
            write_numbered_page(paths[1], "right", "4.8 V")
            old_extract = extract_pdf_text(paths[0], 1, 1)
            new_extract = extract_pdf_text(paths[1], 1, 1)
            options = DiffOptions(
                old_start_page=1,
                old_end_page=1,
                new_start_page=1,
                new_end_page=1,
            )
            result = run_diff(paths[0], paths[1], options)
            reports = write_reports(result, root / "reports", options)
            payload = json.loads(reports["json"].read_text(encoding="utf-8"))
            reader_changes = json.dumps(payload["changes"], ensure_ascii=False)

        self.assertFalse(old_extract.pages[0].ambiguous_line_number_sides)
        self.assertFalse(new_extract.pages[0].ambiguous_line_number_sides)
        self.assertNotIn("\n1 2.9.4", "\n" + old_extract.pages[0].text)
        self.assertIn("2.9.4 Transmitter Signal Quality Test", old_extract.pages[0].text)
        self.assertIn("2.9.4 Transmitter Signal Quality Test", new_extract.pages[0].text)
        self.assertIn("4.2 V", reader_changes)
        self.assertIn("4.8 V", reader_changes)
        self.assertEqual(["modified"], [change["change_type"] for change in payload["changes"]])

    def test_single_page_copyright_footer_style_change_is_not_technical_content(self) -> None:
        """A publisher URL/legal notice swap at page bottom stays metadata."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old-footer.pdf"
            new_path = root / "new-footer.pdf"
            shared_body = (
                "32.4 References",
                "The test method preserves the specified receiver limit.",
            )
            old = fitz.open()
            old_page = old.new_page(width=612, height=792)
            old_page.insert_text((72, 160), shared_body[0], fontsize=10)
            old_page.insert_text((72, 190), shared_body[1], fontsize=10)
            old_page.insert_text(
                (72, 725),
                "Optical Internetworking Forum - Clause 32: CEI-224G-MR Interface",
                fontsize=8,
            )
            old_page.insert_text((72, 740), "www.oiforum.com", fontsize=8)
            old_page.insert_text(
                (72, 755),
                "This is a draft and not to be shared before publication approval.",
                fontsize=7,
            )
            old.save(old_path)
            old.close()
            new = fitz.open()
            new_page = new.new_page(width=612, height=792)
            new_page.insert_text((72, 160), shared_body[0], fontsize=10)
            new_page.insert_text((72, 190), shared_body[1], fontsize=10)
            new_page.insert_text(
                (72, 725),
                "Optical Internetworking Forum - Clause 32: CEI-224G-MR Interface",
                fontsize=8,
            )
            new_page.insert_text(
                (72, 740),
                "Copyright © 2026 Optical Internetworking Forum",
                fontsize=8,
            )
            new_page.insert_text(
                (72, 755),
                "This is a draft and not to be shared before publication approval.",
                fontsize=7,
            )
            new.save(new_path)
            new.close()
            options = DiffOptions(
                old_start_page=1,
                old_end_page=1,
                new_start_page=1,
                new_end_page=1,
            )
            result = run_diff(old_path, new_path, options)
            reports = write_reports(result, root / "reports", options)
            payload = json.loads(reports["json"].read_text(encoding="utf-8"))
            reader_changes = json.dumps(payload["content_changes"], ensure_ascii=False)

        self.assertFalse(payload["content_changes"], reader_changes)
        self.assertNotIn("oiforum.com", reader_changes.casefold())
        self.assertNotIn("copyright © 2026", reader_changes.casefold())

    @unittest.skipUnless(
        _REAL_OIF_CEI_51.is_file() and _REAL_OIF_CEI_053.is_file(),
        "local OIF CEI 5.1/5.3 PDFs are unavailable",
    )
    def test_real_oif_single_page_figure_text_reflow_is_not_a_confirmed_change(self) -> None:
        """The one-page window keeps an identical Figure as visual review, not prose/table edits."""

        options = DiffOptions(
            old_start_page=105,
            old_end_page=105,
            new_start_page=109,
            new_end_page=109,
        )
        result = run_diff(_REAL_OIF_CEI_51, _REAL_OIF_CEI_053, options)
        with tempfile.TemporaryDirectory() as temp_dir:
            reports = write_reports(result, Path(temp_dir), options)
            payload = json.loads(reports["json"].read_text(encoding="utf-8"))

        figure_groups = [
            group
            for group in payload["prose_source_visuals"]
            if group["change_type"] == "figure"
        ]
        self.assertEqual("degraded", payload["assessment"]["state"])
        extraction_warnings = [
            *payload["assessment"]["old_document"]["extraction_warnings"],
            *payload["assessment"]["new_document"]["extraction_warnings"],
        ]
        self.assertFalse(
            any("至少三页" in warning for warning in extraction_warnings),
            extraction_warnings,
        )
        self.assertEqual([], payload["content_changes"])
        self.assertEqual([], payload["content_table_changes"])
        self.assertTrue(
            any(
                group["old_figure_pages"] == [105]
                and group["new_figure_pages"] == [109]
                and group["old_figure_captions"]
                == ["Figure 2-21. Varying the Receiver Sampling Point"]
                and group["new_figure_captions"]
                == ["Figure 2-21. Varying the Receiver Sampling Point"]
                for group in figure_groups
            ),
            json.dumps(figure_groups, ensure_ascii=False),
        )

    @unittest.skipUnless(
        _REAL_OIF_CEI_40.is_file() and _REAL_OIF_CEI_51.is_file(),
        "local OIF CEI 4.0/5.1 PDFs are unavailable",
    )
    def test_real_oif_ce40_to_ce51_two_page_gutter_style_does_not_report_body_changes(self) -> None:
        """Two facing-page line-number gutters must not become reader prose changes."""

        options = DiffOptions(
            old_start_page=224,
            old_end_page=225,
            new_start_page=238,
            new_end_page=239,
        )
        result = run_diff(_REAL_OIF_CEI_40, _REAL_OIF_CEI_51, options)
        with tempfile.TemporaryDirectory() as temp_dir:
            reports = write_reports(result, Path(temp_dir) / "reports", options)
            payload = json.loads(reports["json"].read_text(encoding="utf-8"))

        self.assertEqual("degraded", payload["assessment"]["state"])
        self.assertEqual([], payload["content_changes"], payload["content_changes"])
        self.assertEqual([], payload["content_table_changes"])
        self.assertFalse(
            any(
                change["change_type"] in {"added", "deleted", "modified"}
                for change in payload["table_changes"]
            ),
            payload["table_changes"],
        )

        def clipped_source_word_bag(document: fitz.Document, page_number: int) -> Counter[str]:
            page = document[page_number - 1]
            words = Counter(
                word[4].casefold()
                for word in page.get_text("words", clip=fitz.Rect(70, 60, 545, 730))
            )
            if words["t"] and words["ransmitter"]:
                words["t"] -= 1
                words["ransmitter"] -= 1
                words["transmitter"] += 1  # old PDF splits this caption word across distant text blocks.
                if not words["t"]:
                    del words["t"]
                if not words["ransmitter"]:
                    del words["ransmitter"]
            return words

        with fitz.open(_REAL_OIF_CEI_40) as old_doc, fitz.open(_REAL_OIF_CEI_51) as new_doc:
            for old_page, new_page in ((224, 238), (225, 239)):
                self.assertEqual(
                    clipped_source_word_bag(old_doc, old_page),
                    clipped_source_word_bag(new_doc, new_page),
                    f"source body/table words differ on old p.{old_page} and new p.{new_page}",
                )
            old_caption_text = old_doc[223].get_text("text")
            new_caption_text = new_doc[237].get_text("text")
            self.assertIn("Table 10-6. T", old_caption_text)
            self.assertIn("ransmitter Electrical Output Specification", old_caption_text)
            self.assertIn("Table 10-6. Transmitter Electrical Output Specification", new_caption_text)

    def test_repeated_forum_clause_footer_requires_stable_folio_backing(self) -> None:
        """Two consecutive folio-backed Forum/Clause captions prove a footer."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "oif-footer.pdf"
            document = fitz.open()
            for page_number in range(1, 3):
                page = document.new_page(width=612, height=792)
                page.insert_text((54, 120), f"11.3 Requirement {page_number}", fontsize=12)
                page.insert_text((54, 150), "The receiver shall preserve the declared limit.", fontsize=10)
                if page_number % 2:
                    page.insert_text(
                        (80, 725),
                        "Optical Internetworking Forum - Clause 11: CEI-25G-LR Interface",
                        fontsize=8,
                    )
                    page.insert_text((540, 725), str(page_number), fontsize=8)
                else:
                    page.insert_text((72, 725), str(page_number), fontsize=8)
                    page.insert_text(
                        (91.5, 725),
                        "Optical Internetworking Forum - Clause 11: CEI-25G-LR Interface",
                        fontsize=8,
                    )
            document.save(path)
            document.close()
            result = extract_pdf_text(path, 1, 2)

        self.assertTrue(
            all(
                "Optical Internetworking Forum" not in page.text
                for page in result.pages
            )
        )
        self.assertTrue(
            all(
                any("Optical Internetworking Forum" in footer for footer in page.running_footer_texts)
                for page in result.pages
            )
        )

    def test_explicit_bottom_draft_sharing_notice_is_metadata_not_body(self) -> None:
        """Only the distinctive bottom-margin OIF draft notice is split from body text."""

        notice = (
            "This is a draft and not to be shared. The DRAFT watermark is not to be removed "
            "until publication or with OIF BoD approval."
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old.pdf"
            new_path = root / "new.pdf"
            old = fitz.open()
            old_page = old.new_page(width=612, height=792)
            old_page.insert_text((54, 120), "32.4 References", fontsize=12)
            old_page.insert_text((54, 150), "Reference placeholder remains unchanged.", fontsize=12)
            old_page.insert_text((72, 725), "Optical Internetworking Forum - Clause 32: CEI-224G-MR-PAM4 Medium Reach Interface", fontsize=8)
            old_page.insert_text((529, 725), "1", fontsize=8)
            old.save(old_path)
            old.close()
            new = fitz.open()
            new_page = new.new_page(width=612, height=792)
            new_page.insert_text((54, 120), "32.4 References", fontsize=12)
            new_page.insert_text((54, 150), "Reference placeholder remains unchanged.", fontsize=12)
            new_page.insert_text((72, 725), "Optical Internetworking Forum - Clause 32: CEI-224G-MR-PAM4 Medium Reach Interface", fontsize=8)
            new_page.insert_text((529, 725), "1", fontsize=8)
            new_page.insert_text((72, 742), "Copyright © 2026 Optical Internetworking Forum", fontsize=8)
            new_page.insert_text((72, 763), notice, fontsize=7)
            new.save(new_path)
            new.close()
            body_notice_path = root / "body_notice.pdf"
            body_notice = fitz.open()
            body_notice_page = body_notice.new_page(width=612, height=792)
            body_notice_page.insert_text((54, 120), "32.4 References", fontsize=12)
            body_notice_page.insert_text((54, 150), notice, fontsize=8)
            body_notice.save(body_notice_path)
            body_notice.close()

            old_result = extract_pdf_text(old_path, 1, 1)
            new_result = extract_pdf_text(new_path, 1, 1)
            body_notice_result = extract_pdf_text(body_notice_path, 1, 1)
            old_page_text = old_result.pages[0].text
            new_page_text = new_result.pages[0].text
            new_footer = " ".join(new_result.pages[0].running_footer_texts)
            options = DiffOptions(
                old_start_page=1,
                old_end_page=1,
                new_start_page=1,
                new_end_page=1,
            )
            result = run_diff(old_path, new_path, options)
            reports = write_reports(result, root / "reports", options)
            data = json.loads(reports["json"].read_text(encoding="utf-8"))

        self.assertNotIn("This is a draft and not to be shared", old_page_text)
        self.assertNotIn("This is a draft and not to be shared", new_page_text)
        self.assertIn("This is a draft and not to be shared", new_footer)
        self.assertIn("Reference placeholder remains unchanged", new_page_text)
        self.assertTrue(new_result.pages[0].page_identity_noise_bboxes)
        self.assertIn("This is a draft and not to be shared", body_notice_result.pages[0].text)
        self.assertFalse(body_notice_result.pages[0].running_footer_texts)
        self.assertFalse(any(
            notice in str(change)
            for change in data["content_changes"]
        ))
        self.assertTrue(any(
            change.get("role") == "document_metadata"
            and notice in str(change)
            for change in data["changes"]
        ))

    def test_short_window_header_matches_only_the_same_document_first_page_identity(self) -> None:
        """A short slice can use its own cover title; unrelated header IDs remain text."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old.pdf"
            new_path = root / "new.pdf"
            unrelated_path = root / "unrelated.pdf"
            _write_pages(
                old_path,
                "Implementation Agreement OIF-CEI-06.0 Common Electrical I/O (CEI)",
                ["Page 1", "Page 2"],
                ["The operating requirement is unchanged.", "The receiver shall meet the limit."],
            )
            _write_pages(
                new_path,
                "Implementation Agreement OIF-CEI-06 Common Electrical I/O (CEI)",
                ["Page 1", "Page 2"],
                ["The operating requirement is unchanged.", "The receiver shall meet the limit."],
            )
            _write_pages(
                unrelated_path,
                "General Protocol Documentation",
                ["Page 1", "Page 2"],
                ["The operating requirement is unchanged.", "The receiver shall meet the limit."],
            )
            # Replace only the second page's masthead with an unrelated technical identifier.
            document = fitz.open(unrelated_path)
            page = document[1]
            page.add_redact_annot(fitz.Rect(30, 8, 580, 38), fill=(1, 1, 1))
            page.apply_redactions()
            page.insert_text((36, 22), "MODE_FAST", fontsize=10)
            document.save(unrelated_path.with_suffix(".edited.pdf"))
            document.close()

            old = extract_pdf_text(old_path, 1, 2)
            new = extract_pdf_text(new_path, 1, 2)
            unrelated = extract_pdf_text(unrelated_path.with_suffix(".edited.pdf"), 1, 2)

        self.assertNotIn("Implementation Agreement OIF-CEI-06.0", old.pages[1].text)
        self.assertNotIn("Implementation Agreement OIF-CEI-06 Common", new.pages[1].text)
        self.assertTrue(any(
            "Implementation Agreement OIF-CEI-06.0" in line
            for line in old.pages[1].page_identity_header_texts
        ))
        self.assertTrue(any(
            "Implementation Agreement OIF-CEI-06 Common" in line
            for line in new.pages[1].page_identity_header_texts
        ))
        self.assertIn("MODE_FAST", unrelated.pages[1].text)
        self.assertEqual((), unrelated.pages[1].page_identity_header_texts)

    def test_short_window_first_page_identity_header_is_suppressed_from_reader_changes(self) -> None:
        """A parity-formatted masthead remains auditable but is not a technical delta."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old_oif.pdf"
            new_path = root / "new_oif.pdf"

            def write_document(path: Path, titles: tuple[str, str]) -> None:
                document = fitz.open()
                for page_number, title in enumerate(titles, start=1):
                    page = document.new_page(width=612, height=792)
                    page.insert_text((135, 26), title, fontsize=10)
                    page.insert_text((54, 112), f"32.{page_number} References", fontsize=12)
                    page.insert_text((54, 150), "Reference text is the same.", fontsize=12)
                    page.insert_text((528, 756), str(page_number), fontsize=9)
                document.save(path)
                document.close()

            write_document(
                old_path,
                (
                    "Implementation Agreement OIF-CEI-6.0 Common Electrical I/O (CEI)",
                    "Implementation Agreement OIF-CEI-06.0 Common Electrical I/O (CEI)",
                ),
            )
            write_document(
                new_path,
                (
                    "Implementation Agreement OIF-CEI-06 Common Electrical I/O (CEI)",
                    "Implementation Agreement OIF-CEI-06 Common Electrical I/O (CEI)",
                ),
            )
            options = DiffOptions(
                old_start_page=1,
                old_end_page=2,
                new_start_page=1,
                new_end_page=2,
            )
            result = run_diff(old_path, new_path, options)
            reports = write_reports(result, root / "reports", options)
            data = json.loads(reports["json"].read_text(encoding="utf-8"))

        self.assertFalse(any(
            change.get("old_location") == "运行页眉（坐标证据）"
            or change.get("new_location") == "运行页眉（坐标证据）"
            for change in data["content_changes"]
        ))
        header_audit = [
            change for change in data["changes"]
            if change.get("old_location") == "运行页眉（坐标证据）"
            or change.get("new_location") == "运行页眉（坐标证据）"
        ]
        self.assertTrue(header_audit)
        self.assertTrue(all(change.get("reader_suppression_reason") for change in header_audit))

    def test_unmatched_table_candidate_is_suppressed_only_for_exact_page_pair(self) -> None:
        """A one-sided table detector result is not a deletion after source-page proof."""

        old_table = TableVisual(
            page_number=1517,
            table_number=1,
            title="",
            bbox=(0.0, 0.0, 612.0, 792.0),
            image_data_uri="",
            row_texts=["page-wide detector candidate"],
            grid_summary="",
        )
        candidate = TableChange(
            change_type="deleted",
            old_tables=(old_table,),
            new_tables=(),
            similarity=0.0,
            caption_changed=False,
            row_changes=(),
        )

        self.assertTrue(_table_change_has_rendered_equal_sources(candidate, {1517: 1567}))
        self.assertFalse(_table_change_has_rendered_equal_sources(candidate, {}))

    def test_pixel_suppressed_raw_json_candidates_are_serialized_as_review(self) -> None:
        """Raw JSON must not label pixel-identical parser candidates as confirmed adds/deletes."""

        audit = VisualWatchdogAudit(
            enabled=True,
            attempted=True,
            backend_available=True,
            eligible_page_pair_count=1,
            checked_page_pair_count=1,
            failed_page_pair_count=0,
            ambiguous_page_count=0,
            excluded_region_count=0,
            complete=True,
            source_hashes_match=True,
            old_visual_source_sha256="a" * 64,
            new_visual_source_sha256="b" * 64,
            identical_body_page_pairs=((1422, 1472),),
            identity_render_dpi=144,
        )
        body = "The source page retains the same visible protocol content."

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old.pdf"
            new_path = root / "new.pdf"
            old_digest = _write_pages(
                old_path,
                "PCI Express Base Specification Revision 6.4",
                ["Page 1422"],
                [body],
            )
            new_digest = _write_pages(
                new_path,
                "PCI Express Base Specification Revision 6.5",
                ["Page 1472"],
                [body],
            )
            for direction, pixel_pair_proven in (
                ("deleted", True),
                ("added", True),
                ("deleted", False),
                ("added", False),
            ):
                with self.subTest(direction=direction, pixel_pair_proven=pixel_pair_proven):
                    old_table = self._window_table(
                        1422,
                        1,
                        (60.0, 100.0, 550.0, 700.0),
                        "Bit Location | Register Description | Attributes | 3 NPEM Locate Control",
                    )
                    new_table = self._window_table(
                        1472,
                        1,
                        (60.0, 100.0, 550.0, 700.0),
                        "Bit Location | Register Description | Attributes | 3 NPEM Locate Control",
                    )
                    old_tables = [old_table] if direction == "deleted" else []
                    new_tables = [new_table] if direction == "added" else []
                    result = compare_extractions(
                        _extraction(old_path, old_digest, [_page_text(1422, body)]),
                        _extraction(new_path, new_digest, [_page_text(1472, body)]),
                        DiffOptions(),
                    )
                    result = replace(
                        result,
                        old_table_visuals=old_tables,
                        new_table_visuals=new_tables,
                        provenance=replace(
                            result.provenance,
                            visual_watchdog_audit=(
                                audit
                                if pixel_pair_proven
                                else replace(audit, identical_body_page_pairs=())
                            ),
                        ),
                    )
                    outputs = write_reports(
                        result,
                        root / f"reports-{direction}",
                        DiffOptions(),
                    )
                    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
                    candidates = [
                        change
                        for change in payload["table_changes"]
                        if change.get("old_pages") == ([1422] if direction == "deleted" else [])
                        and change.get("new_pages") == ([1472] if direction == "added" else [])
                    ]
                    csv_rows = csv.DictReader(
                        io.StringIO(outputs["table_csv"].read_text(encoding="utf-8-sig"))
                    )
                    csv_types = {row["table_change_type"] for row in csv_rows}
                    self.assertEqual(1, len(candidates))
                    candidate = candidates[0]
                    if pixel_pair_proven:
                        self.assertEqual("review", candidate["change_type"])
                        self.assertEqual("需复核", candidate["change_label"])
                        self.assertEqual(direction, candidate["raw_candidate_change_type"])
                        self.assertEqual(
                            "删除" if direction == "deleted" else "新增",
                            candidate["raw_candidate_change_label"],
                        )
                        self.assertEqual("suppressed", candidate["reader_disposition"])
                        self.assertTrue(candidate["reader_suppression_reason"])
                        self.assertEqual(0, candidate["row_change_count"])
                        self.assertEqual(1, candidate["review_count"])
                        self.assertEqual("需人工复核", candidate["row_changes"][0]["change_type"])
                        self.assertIn(
                            "删除行" if direction == "deleted" else "新增行",
                            candidate["row_changes"][0]["raw_candidate_change_type"],
                        )
                        excerpt = (
                            candidate["row_changes"][0]["old_value"]
                            if direction == "deleted"
                            else candidate["row_changes"][0]["new_value"]
                        )
                        self.assertIn("NPEM Locate Control", excerpt)
                        self.assertNotIn("删除", csv_types)
                        self.assertNotIn("新增", csv_types)
                    else:
                        self.assertEqual(direction, candidate["change_type"])
                        self.assertNotIn("reader_disposition", candidate)
                        self.assertIn(
                            "删除" if direction == "deleted" else "新增",
                            csv_types,
                        )

    def test_real_body_value_change_remains_in_reader_report(self) -> None:
        """A visible technical value change is not suppressed as page furniture."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old_value.pdf"
            new_path = root / "new_value.pdf"
            _write_pages(
                old_path,
                "PCI Express Base Specification Revision 6.4",
                ["Page 1"],
                ["The calibrated maximum is 8 ns."],
            )
            _write_pages(
                new_path,
                "PCI Express Base Specification Revision 6.5",
                ["Page 1"],
                ["The calibrated maximum is 9 ns."],
            )

            result = run_diff(old_path, new_path, DiffOptions())
            outputs = write_reports(result, root / "reports", DiffOptions())
            report_text = outputs["markdown"].read_text(encoding="utf-8")

        self.assertIn("8 ns", report_text)
        self.assertIn("9 ns", report_text)
        self.assertTrue(result.changes)
        self.assertFalse(result.assessment.allows_no_difference_conclusion)
        self.assertEqual((), result.provenance.visual_watchdog_audit.identical_body_page_pairs)

    @staticmethod
    def _folio_words(page_number: int, side: str, *, top: float = 770.0):
        left = 7.0 if side == "left" else 560.0
        return [
            {"text": "Page", "x0": left, "x1": left + 20.0, "top": top, "bottom": top + 9.0},
            {"text": str(page_number), "x0": left + 22.0, "x1": left + 45.0, "top": top, "bottom": top + 9.0},
        ]

    def test_single_matching_page_label_is_only_a_candidate(self) -> None:
        """A true physical number at the bottom still needs document evidence."""

        page = SimpleNamespace(width=612.0, height=792.0)
        words = self._folio_words(1517, "right")

        self.assertIsNotNone(_physical_page_folio_candidate(page, 1517, words))
        self.assertEqual(
            {},
            _document_physical_page_folio_evidence([(1517, page)], {1517: words}),
        )  # Even an exact bottom-right `Page 1517` line is not filtered alone.

    def test_document_folio_proof_requires_continuity_baseline_and_parity_slots(self) -> None:
        """Only a repeated continuous sequence in stable alternating slots is filtered."""

        pages = [(number, SimpleNamespace(width=612.0, height=792.0))
                 for number in range(1517, 1520)]
        words_by_page = {
            number: self._folio_words(number, "right" if number % 2 else "left")
            for number, _page in pages
        }

        evidence = _document_physical_page_folio_evidence(pages, words_by_page)

        self.assertEqual({1517, 1518, 1519}, set(evidence))
        self.assertEqual("Page 1517", evidence[1517][1])
        self.assertEqual(2, len(evidence[1517][0]))
        self.assertEqual(
            {},
            _document_physical_page_folio_evidence(
                pages,
                {**words_by_page, 1518: self._folio_words(1516, "left")},
            ),
        )  # 一个错误页号打破连续序列，剩余候选达不到 80% 覆盖。
        self.assertEqual(
            {},
            _document_physical_page_folio_evidence(
                pages,
                {number: self._folio_words(number, "right") for number, _page in pages},
            ),
        )  # 所有标签都挤在同一侧时，不符合奇偶页版心交替证据。
        self.assertEqual(
            {},
            _document_physical_page_folio_evidence(
                pages,
                {
                    **words_by_page,
                    1519: self._folio_words(1519, "right", top=760.0),
                },
            ),
        )  # 页脚纵向位置漂移时 fail-open，保留原文比较。

    def test_document_folio_boxes_reach_text_filter_and_sectioning_fails_open(self) -> None:
        """Only strict document evidence removes Page N from source text."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            proven_path = root / "proven_folios.pdf"
            _write_positioned_folio_pages(
                proven_path,
                [
                    ("Page 1", "right"),
                    ("Page 2", "left"),
                    ("Page 3", "right"),
                ],
            )
            proven = extract_pdf_text(proven_path, 1, 3)

            self.assertEqual(
                [(1, ("Page 1",)), (2, ("Page 2",)), (3, ("Page 3",))],
                [(page.page_number, page.running_footer_texts) for page in proven.pages],
            )
            self.assertTrue(all("Page " not in page.text for page in proven.pages))

            ambiguous_path = root / "ambiguous_folios.pdf"
            _write_positioned_folio_pages(
                ambiguous_path,
                [
                    ("Page 1", "right"),
                    ("Page 2", "right"),
                    (None, "right"),
                    ("Page 4", "right"),
                ],
            )
            ambiguous = extract_pdf_text(ambiguous_path, 1, 4)
            section_bodies = "\n".join(
                section.body for section in section_document(ambiguous)
            )

        for label in ("Page 1", "Page 2", "Page 4"):
            with self.subTest(label=label):
                self.assertIn(label, section_bodies)
        self.assertTrue(all(not page.running_footer_texts for page in ambiguous.pages))

    def test_reader_equivalent_page_furniture_is_checked_and_body_is_proven_equal(self) -> None:
        """Version mastheads and folios do not block a direct source-page check."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old.pdf"
            new_path = root / "new.pdf"
            old_digest = _write_pages(
                old_path,
                "PCI Express Base Specification Revision 6.4",
                ["Page 1517"],
                ["The receiver preserves the calibrated source-page content."],
            )
            new_digest = _write_pages(
                new_path,
                "PCI Express Base Specification Revision 6.5",
                ["Page 1567"],
                ["The receiver preserves the calibrated source-page content."],
            )
            old = _extraction(
                old_path,
                old_digest,
                [_page_text(1, "The receiver preserves the calibrated source-page content.\nPage 1517")],
            )
            new = _extraction(
                new_path,
                new_digest,
                [_page_text(1, "The receiver preserves the calibrated source-page content.\nPage 1567")],
            )

            items, _warnings, audit = detect_visual_review_items(old, new)

        self.assertEqual([], items)
        self.assertEqual(1, audit.eligible_page_pair_count)
        self.assertEqual(1, audit.checked_page_pair_count)
        self.assertEqual(0, audit.failed_page_pair_count)
        self.assertTrue(audit.complete)
        self.assertEqual(((1, 1),), audit.identical_body_page_pairs)

    def test_publication_version_header_stays_out_of_reader_changes_when_body_changes(self) -> None:
        """A real body edit must not make the verified publication header substantive."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old_publication_header.pdf"
            new_path = root / "new_publication_header.pdf"
            _write_publication_header_pages(
                old_path,
                "6.4",
                ["The unchanged opening clause.", "The specified offset is 8 ns.", "The unchanged closing clause."],
            )
            _write_publication_header_pages(
                new_path,
                "6.5",
                ["The unchanged opening clause.", "The specified offset is 9 ns.", "The unchanged closing clause."],
            )

            result = run_diff(old_path, new_path, DiffOptions())
            outputs = write_reports(result, root / "reports", DiffOptions())
            data = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertTrue(any("8 ns" in item.get("old", "") and "9 ns" in item.get("new", "")
                            for change in data["content_changes"]
                            for item in change.get("replaced_snippets", [])))
        header_audit = [
            change for change in data["changes"]
            if change.get("old_location") == "运行页眉（坐标证据）"
        ]
        self.assertTrue(header_audit)
        self.assertTrue(all(change.get("reader_suppression_reason") for change in header_audit))
        self.assertFalse(any(
            change.get("old_location") == "运行页眉（坐标证据）"
            for change in data["content_changes"]
        ))
        self.assertEqual(
            ("6.4-1.0-PUB - PCI Express Base Specification",),
            tuple(data["extraction_audit"]["old"][0]["page_identity_header_texts"]),
        )
        self.assertEqual(
            ("6.5-1.0-PUB - PCI Express Base Specification",),
            tuple(data["extraction_audit"]["new"][0]["page_identity_header_texts"]),
        )

    def test_title_matched_publication_line_is_filtered_from_multiline_running_header(self) -> None:
        """A second repeated top line cannot make a Title-proven revision banner look substantive."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_bodies = [
                "The unchanged opening clause.",
                "The specified offset is 8 ns.",
                "The unchanged closing clause.",
            ]
            new_bodies = [
                "The unchanged opening clause.",
                "The specified offset is 9 ns.",
                "The unchanged closing clause.",
            ]

            def compare_additional_header(
                new_line: str,
                *,
                old_line: str = "Name Affiliation",
                old_banner_prefix: str | None = None,
                new_banner_prefix: str | None = None,
                pair_header_sections: bool = False,
            ):
                old_path = root / f"old_header_{new_line.replace(' ', '_')}.pdf"
                new_path = root / f"new_header_{new_line.replace(' ', '_')}.pdf"
                _write_publication_header_pages(
                    old_path,
                    "6.4",
                    old_bodies,
                    additional_running_header_line=old_line,
                    banner_prefix=old_banner_prefix,
                )
                _write_publication_header_pages(
                    new_path,
                    "6.5",
                    new_bodies,
                    additional_running_header_line=new_line,
                    banner_prefix=new_banner_prefix,
                )
                result = run_diff(old_path, new_path, DiffOptions())
                if pair_header_sections:
                    old_header = next(
                        section
                        for section in result.old_sections
                        if section.section_id == "running-header-evidence"
                    )
                    new_header = next(
                        section
                        for section in result.new_sections
                        if section.section_id == "running-header-evidence"
                    )
                    non_header_changes = [
                        change
                        for change in result.changes
                        if not any(
                            section is not None
                            and section.section_id == "running-header-evidence"
                            for section in (change.old_section, change.new_section)
                        )
                    ]
                    result = replace(
                        result,
                        changes=[
                            *non_header_changes,
                            SectionChange(
                                change_type="modified",
                                old_section=old_header,
                                new_section=new_header,
                                similarity=0.0,
                                replaced_snippets=[
                                    SnippetPair(old_header.body, new_header.body)
                                ],
                            ),
                        ],
                    )
                outputs = write_reports(result, root / f"reports-{new_line.replace(' ', '_')}", DiffOptions())
                return json.loads(outputs["json"].read_text(encoding="utf-8"))

            unchanged_extra_line = compare_additional_header("Name Affiliation")
            changed_extra_line = compare_additional_header("Name Role")
            invalid_prefix = compare_additional_header(
                "Name Affiliation",
                old_banner_prefix="ALPHA-1.0-PUB",
                new_banner_prefix="BETA-1.0-PUB",
            )
            duplicate_banner = compare_additional_header(
                "6.5-1.0-PUB - PCI Express Base Specification",
                old_line="6.4-1.0-PUB - PCI Express Base Specification",
            )
            embedded_banner_line = compare_additional_header(
                "Editorial notice: 6.5-1.0-PUB - PCI Express Base Specification",
                old_line="Editorial notice: 6.4-1.0-PUB - PCI Express Base Specification",
                pair_header_sections=True,
            )

        version_header_audit = [
            change
            for change in unchanged_extra_line["changes"]
            if change.get("old_location") == "运行页眉（坐标证据）"
        ]
        self.assertTrue(version_header_audit)
        self.assertTrue(all(change.get("reader_suppression_reason") for change in version_header_audit))
        self.assertTrue(
            all(change.get("role") == "document_metadata" for change in version_header_audit),
            version_header_audit,
        )
        self.assertFalse(any(
            change.get("old_location") == "运行页眉（坐标证据）"
            for change in unchanged_extra_line["content_changes"]
        ))
        self.assertTrue(any(
            "8 ns" in pair.get("old", "") and "9 ns" in pair.get("new", "")
            for change in unchanged_extra_line["content_changes"]
            for pair in change.get("replaced_snippets", [])
        ))
        self.assertEqual(
            ("6.4-1.0-PUB - PCI Express Base Specification",),
            tuple(unchanged_extra_line["extraction_audit"]["old"][0]["page_identity_header_texts"]),
        )
        self.assertEqual(
            ("6.5-1.0-PUB - PCI Express Base Specification",),
            tuple(unchanged_extra_line["extraction_audit"]["new"][0]["page_identity_header_texts"]),
        )
        changed_header_text = [
            snippet
            for change in changed_extra_line["content_changes"]
            if change.get("old_location") == "运行页眉（坐标证据）"
            or change.get("new_location") == "运行页眉（坐标证据）"
            for snippet in (
                *change.get("added_snippets", []),
                *change.get("removed_snippets", []),
                *(f"{item.get('old', '')} {item.get('new', '')}" for item in change.get("replaced_snippets", [])),
            )
        ]
        self.assertTrue(any("Name Role" in snippet for snippet in changed_header_text))
        self.assertTrue(any("Name Affiliation" in snippet for snippet in changed_header_text))
        changed_header_audit = [
            change
            for change in changed_extra_line["changes"]
            if change.get("old_location") == "运行页眉（坐标证据）"
            or change.get("new_location") == "运行页眉（坐标证据）"
        ]
        self.assertTrue(changed_header_audit)
        self.assertTrue(any(change.get("role") == "technical" for change in changed_header_audit))

        for ambiguous_case, expected_text, identity_was_proven in (
            (invalid_prefix, "ALPHA-1.0-PUB", False),
            (duplicate_banner, "6.4-1.0-PUB - PCI Express Base Specification", False),
            (embedded_banner_line, "Editorial notice:", True),
        ):
            with self.subTest(expected_text=expected_text):
                header_reader_changes = [
                    change
                    for change in ambiguous_case["content_changes"]
                    if change.get("old_location") == "运行页眉（坐标证据）"
                    or change.get("new_location") == "运行页眉（坐标证据）"
                ]
                if expected_text == "Editorial notice:":
                    self.assertTrue(
                        header_reader_changes,
                        "MULTILINE_HEADER_IDENTITY_SUBSTRING_LEAKED",
                    )
                else:
                    self.assertTrue(header_reader_changes)
                self.assertTrue(all(
                    bool(audit.get("page_identity_header_texts")) is identity_was_proven
                    for side in ("old", "new")
                    for audit in ambiguous_case["extraction_audit"][side]
                ))
                header_snippets = [
                    snippet
                    for change in header_reader_changes
                    for snippet in (
                        *change.get("added_snippets", []),
                        *change.get("removed_snippets", []),
                        *(f"{item.get('old', '')} {item.get('new', '')}" for item in change.get("replaced_snippets", [])),
                    )
                ]
                self.assertTrue(any(expected_text in snippet for snippet in header_snippets))
                if expected_text == "Editorial notice:":
                    self.assertTrue(any(
                        "Editorial notice: 6.4-1.0-PUB - PCI Express Base Specification" in snippet
                        for snippet in header_snippets
                    ))
                    self.assertTrue(any(
                        "Editorial notice: 6.5-1.0-PUB - PCI Express Base Specification" in snippet
                        for snippet in header_snippets
                    ))

    def test_paired_header_suppression_fails_open_when_identity_row_is_missing_or_duplicated(self) -> None:
        """Missing or duplicate exact banner rows cannot compare as two equal None values."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_pdf = root / "old_header.pdf"
            new_pdf = root / "new_header.pdf"
            bodies = ["Opening clause.", "Stable technical value: 8 ns.", "Closing clause."]
            _write_publication_header_pages(
                old_pdf,
                "6.4",
                bodies,
                additional_running_header_line="Name Affiliation",
            )
            _write_publication_header_pages(
                new_pdf,
                "6.5",
                bodies,
                additional_running_header_line="Name Affiliation",
            )
            result = run_diff(old_pdf, new_pdf, DiffOptions())

        old_header = next(
            section
            for section in result.old_sections
            if section.section_id == "running-header-evidence"
        )
        new_header = next(
            section
            for section in result.new_sections
            if section.section_id == "running-header-evidence"
        )
        self.assertTrue(all(
            page.page_identity_header_texts == (
                "6.4-1.0-PUB - PCI Express Base Specification",
            )
            for page in result.old_extraction_audit
        ))
        self.assertTrue(all(
            page.page_identity_header_texts == (
                "6.5-1.0-PUB - PCI Express Base Specification",
            )
            for page in result.new_extraction_audit
        ))

        old_banner = "6.4-1.0-PUB - PCI Express Base Specification"
        new_banner = "6.5-1.0-PUB - PCI Express Base Specification"
        body_cases = (
            (
                "duplicate exact rows",
                f"{old_banner}\n{old_banner}\nOld note",
                f"{new_banner}\n{new_banner}\nNew note",
            ),
            ("missing exact rows", "Old note", "New note"),
            ("both sides unresolved differently", "Old note", f"{new_banner}\nNew note"),
        )
        for case_name, old_body, new_body in body_cases:
            with self.subTest(case_name=case_name):
                change = SectionChange(
                    change_type="modified",
                    old_section=replace(old_header, body=old_body),
                    new_section=replace(new_header, body=new_body),
                    similarity=0.0,
                )
                paired_result = replace(result, changes=[change])
                self.assertFalse(
                    _proven_publication_header_section_change(change, paired_result),
                    "MULTILINE_HEADER_IDENTITY_NONUNIQUE_LEAKED",
                )

    @staticmethod
    def _page_audit(page_number: int) -> PageExtractionAudit:
        return PageExtractionAudit(
            page_number=page_number,
            parser_route=PageParserRoute.NATIVE_TEXT,
            image_dominant=False,
            ocr_used=False,
            layout_risk=False,
            block_count=3,
            running_footer_texts=(f"Page {page_number}",),
        )

    @staticmethod
    def _window_table(page_number: int, table_number: int, bbox, text: str) -> TableVisual:
        return TableVisual(
            page_number=page_number,
            table_number=table_number,
            title="",
            bbox=bbox,
            image_data_uri="data:image/jpeg;base64,",
            row_texts=[text],
            grid_summary="",
        )

    def test_single_changed_page_between_exact_neighbors_does_not_leak_table_add_delete(self) -> None:
        """Geometry-matched parser candidates on an anchored changed page stay review-only."""

        old_start, new_start = 1552, 1602
        old_end, new_end = 1562, 1612
        exact_pairs = tuple(
            (old_page, old_page + 50)
            for old_page in range(old_start, old_end + 1)
            if old_page != 1557
        )
        audit = VisualWatchdogAudit(
            enabled=True,
            attempted=True,
            backend_available=True,
            eligible_page_pair_count=10,
            checked_page_pair_count=10,
            failed_page_pair_count=0,
            ambiguous_page_count=0,
            excluded_region_count=0,
            complete=True,
            source_hashes_match=True,
            old_visual_source_sha256="a" * 64,
            new_visual_source_sha256="b" * 64,
            identical_body_page_pairs=exact_pairs,
            identity_render_dpi=144,
        )
        old_tables = [
            self._window_table(1557, 1, (3.1, 4.3, 610.9, 792.0), "full-page candidate with one changed word: vold"),
            self._window_table(1557, 2, (139.7, 49.0, 257.2, 237.1), "Min=5 % | Max=50 %"),
            self._window_table(1557, 3, (139.7, 372.0, 257.2, 694.6), "Column 1=0 | Column 2=63"),
        ]
        new_tables = [
            self._window_table(1607, 1, (3.1, 4.3, 610.9, 792.0), "full-page candidate with one changed word: volt"),
            self._window_table(1607, 2, (139.7, 49.0, 257.2, 237.1), "Min=5 % | Max=50 %"),
            self._window_table(1607, 3, (139.7, 372.0, 257.2, 694.6), "Column 1=0 | Column 2=63"),
        ]
        result = DiffResult(
            old_pdf=Path("old-window.pdf"),
            new_pdf=Path("new-window.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_selected_start_page=old_start,
            old_selected_end_page=old_end,
            new_selected_start_page=new_start,
            new_selected_end_page=new_end,
            old_table_visuals=old_tables,
            new_table_visuals=new_tables,
            provenance=SimpleNamespace(
                visual_watchdog_audit=audit,
                old_input=SimpleNamespace(
                    sha256="a" * 64,
                    selected_start_page=old_start,
                    selected_end_page=old_end,
                ),
                new_input=SimpleNamespace(
                    sha256="b" * 64,
                    selected_start_page=new_start,
                    selected_end_page=new_end,
                ),
            ),
            old_extraction_audit=tuple(self._page_audit(page) for page in range(old_start, old_end + 1)),
            new_extraction_audit=tuple(self._page_audit(page) for page in range(new_start, new_end + 1)),
        )

        exact_map = dict(exact_pairs)
        bridges = _selected_window_single_page_visual_bridges(result, exact_map)
        self.assertEqual({1557: 1607}, bridges)
        groups = _paired_table_visuals(
            old_tables,
            new_tables,
            page_pairs=bridges,
        )
        self.assertEqual(3, len(groups))
        self.assertTrue(all(group.review_only for group in groups))
        self.assertTrue(all(group.old_tables and group.new_tables for group in groups))

        changes = _build_table_changes(result, table_groups=groups)
        reader_changes = _reader_table_changes(changes)
        self.assertEqual(["review"] * 3, [change.change_type for change in changes])
        self.assertFalse(any(change.change_type in {"added", "deleted"} for change in reader_changes))
        self.assertEqual(3, len(reader_changes))

        self.assertEqual(
            {},
            _selected_window_single_page_visual_bridges(
                result,
                {old_page: new_page for old_page, new_page in exact_pairs if old_page != 1556},
            ),
        )  # 两侧精确页锚点缺一侧时，不推测中间页对应关系。

    def test_locator_only_table_renumbering_is_reviewed_instead_of_added_and_deleted(self) -> None:
        """Same-window, same-box candidates with only Figure/Table renumbering are not table add/delete facts."""

        old_start, old_end = 1517, 1519
        new_start, new_end = 1567, 1569
        old_path = Path("old_pcie_window.pdf")
        new_path = Path("new_pcie_window.pdf")
        old_table = TableVisual(
            page_number=1518,
            table_number=1,
            title="",
            bbox=(40.0, 100.0, 572.0, 700.0),
            image_data_uri="",
            row_texts=[
                "Table 7-258 DPA Extended Capability Header §; "
                "Figure 7-289 DPA Extended Capability Header §"
            ],
            grid_summary="",
        )
        new_table = TableVisual(
            page_number=1568,
            table_number=1,
            title="",
            bbox=old_table.bbox,
            image_data_uri="",
            row_texts=[
                "Table 7-270 DPA Extended Capability Header §; "
                "Figure 7-301 DPA Extended Capability Header §"
            ],
            grid_summary="",
        )
        provenance = DiffProvenance(
            package_version="test",
            build_commit="test",
            supported_profile="native text test fixture",
            old_input=InputProvenance(old_path, "a" * 64, old_start, old_end),
            new_input=InputProvenance(new_path, "b" * 64, new_start, new_end),
            effective_thresholds=EffectiveThresholds(0.72, 20, 500, 0.2),
        )
        result = DiffResult(
            old_pdf=old_path,
            new_pdf=new_path,
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_total_pages=2229,
            new_total_pages=2283,
            old_selected_start_page=old_start,
            old_selected_end_page=old_end,
            new_selected_start_page=new_start,
            new_selected_end_page=new_end,
            old_table_visuals=[old_table],
            new_table_visuals=[new_table],
            provenance=provenance,
            old_extraction_audit=tuple(
                self._page_audit(page) for page in range(old_start, old_end + 1)
            ),
            new_extraction_audit=tuple(
                self._page_audit(page) for page in range(new_start, new_end + 1)
            ),
            old_total_pages_known=True,
            new_total_pages_known=True,
        )

        self.assertEqual(
            {1517: 1567, 1518: 1568, 1519: 1569},
            _selected_window_folio_page_pairs(result),
        )
        self.assertEqual(
            {},
            _selected_window_folio_page_pairs(
                replace(result, new_extraction_audit=result.new_extraction_audit[:-1])
            ),
        )  # 页窗少一张精确页脚时，不建立按页偏移的候选对应。

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, Path(temp_dir) / "reports", DiffOptions())
            data = json.loads(outputs["json"].read_text(encoding="utf-8"))

        reader_tables = data["content_table_changes"]
        self.assertEqual(["review"], [change["change_type"] for change in reader_tables])
        self.assertFalse(
            any(change["change_type"] in {"added", "deleted"} for change in reader_tables)
        )
        self.assertEqual([1518], reader_tables[0]["old_pages"])
        self.assertEqual([1568], reader_tables[0]["new_pages"])

    def test_selected_window_maps_coordinate_proven_clause_footers(self) -> None:
        """A footer title plus its isolated printed folio can map non-Page-N formats."""

        old_start, old_end = 1517, 1519
        new_start, new_end = 1567, 1569
        title = "Optical Internetworking Forum - Clause 32: CEI-MR Interface"
        old_footers = (
            f"{title} 1517",
            f"1518 {title}",
            f"{title} 1519",
        )
        new_footers = (
            f"{title} 1567",
            f"1568 {title}",
            f"{title} 1569",
        )
        result = DiffResult(
            old_pdf=Path("old-oif.pdf"),
            new_pdf=Path("new-oif.pdf"),
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_selected_start_page=old_start,
            old_selected_end_page=old_end,
            new_selected_start_page=new_start,
            new_selected_end_page=new_end,
            provenance=DiffProvenance(
                package_version="test",
                build_commit="test",
                supported_profile="native text test fixture",
                old_input=InputProvenance(
                    Path("old-oif.pdf"), "a" * 64, old_start, old_end
                ),
                new_input=InputProvenance(
                    Path("new-oif.pdf"), "b" * 64, new_start, new_end
                ),
                effective_thresholds=EffectiveThresholds(0.72, 20, 500, 0.2),
            ),
            old_extraction_audit=tuple(
                replace(self._page_audit(page), running_footer_texts=(old_footers[page-old_start],))
                for page in range(old_start, old_end + 1)
            ),
            new_extraction_audit=tuple(
                replace(self._page_audit(page), running_footer_texts=(new_footers[page-new_start],))
                for page in range(new_start, new_end + 1)
            ),
        )

        self.assertEqual(
            {1517: 1567, 1518: 1568, 1519: 1569},
            _selected_window_folio_page_pairs(result),
        )
        mismatched = replace(
            result,
            new_extraction_audit=(
                result.new_extraction_audit[0],
                replace(
                    result.new_extraction_audit[1],
                    running_footer_texts=("Optical Internetworking Forum - Clause 33: Other Interface 1568",),
                ),
                result.new_extraction_audit[2],
            ),
        )
        self.assertEqual({}, _selected_window_folio_page_pairs(mismatched))

    def test_single_page_review_mapping_requires_distinct_inputs_and_exact_audits(self) -> None:
        """A positional review locator cannot map pages from one PDF or duplicate audit rows."""

        old_start, new_start = 131, 135
        old_path, new_path = Path("old-one-page.pdf"), Path("new-one-page.pdf")
        provenance = DiffProvenance(
            package_version="test",
            build_commit="test",
            supported_profile="native text test fixture",
            old_input=InputProvenance(old_path, "a" * 64, old_start, old_start),
            new_input=InputProvenance(new_path, "b" * 64, new_start, new_start),
            effective_thresholds=EffectiveThresholds(0.72, 20, 500, 0.2),
        )
        result = DiffResult(
            old_pdf=old_path,
            new_pdf=new_path,
            old_sections=[],
            new_sections=[],
            changes=[],
            warnings=[],
            old_selected_start_page=old_start,
            old_selected_end_page=old_start,
            new_selected_start_page=new_start,
            new_selected_end_page=new_start,
            provenance=provenance,
            old_extraction_audit=(self._page_audit(old_start),),
            new_extraction_audit=(self._page_audit(new_start),),
        )

        self.assertEqual({old_start: new_start}, _selected_single_page_window_review_pairs(result))
        same_source = replace(
            result,
            provenance=replace(
                provenance,
                new_input=InputProvenance(new_path, "a" * 64, new_start, new_start),
            ),
        )
        self.assertEqual({}, _selected_single_page_window_review_pairs(same_source))
        duplicated_audit = replace(
            result,
            old_extraction_audit=(self._page_audit(old_start), self._page_audit(old_start)),
        )
        self.assertEqual({}, _selected_single_page_window_review_pairs(duplicated_audit))

    def test_same_position_table_content_change_is_reviewed_not_added_and_deleted(self) -> None:
        """A same-box candidate with changed values needs a visible review card."""

        old = self._window_table(
            1518,
            1,
            (40.0, 100.0, 572.0, 700.0),
            "Table 7-258 DPA Register maximum 8 ns; Figure 7-289 DPA Register",
        )
        candidates = (
            "Table 7-270 DPA Register maximum 9 ns; Figure 7-301 DPA Register",
            "Table 7-271 DPA Register maximum 8 ns; Figure 7-301 DPA Register",
        )
        for new_text in candidates:
            with self.subTest(new_text=new_text):
                new = self._window_table(
                    1568,
                    1,
                    old.bbox,
                    new_text,
                )
                groups = _paired_table_visuals(
                    [old],
                    [new],
                    locator_page_pairs={1518: 1568},
                )
                self.assertEqual(1, len(groups))
                self.assertTrue(groups[0].review_only)
                self.assertTrue(groups[0].old_tables and groups[0].new_tables)
                self.assertEqual("same-position-content-change", groups[0].review_reason)

                result = DiffResult(
                    old_pdf=Path("old-window.pdf"),
                    new_pdf=Path("new-window.pdf"),
                    old_sections=[],
                    new_sections=[],
                    changes=[],
                    warnings=[],
                )
                changes = _build_table_changes(result, table_groups=groups)
                self.assertEqual(["review"], [change.change_type for change in changes])
                self.assertTrue(all(
                    row.change_type == "需人工复核"
                    for row in changes[0].row_changes
                ))
                reader_changes = _reader_table_changes(changes)
                self.assertEqual(["review"], [change.change_type for change in reader_changes])
                if "9 ns" in new_text:
                    self.assertTrue(any(
                        "8 ns" in row.old_value and "9 ns" in row.new_value
                        for row in reader_changes[0].row_changes
                    ))
                else:
                    self.assertTrue(any(
                        "Table 7-258" in row.old_value
                        and "Table 7-271" in row.new_value
                        for row in reader_changes[0].row_changes
                    ))

    def test_single_page_window_reviews_unique_same_box_singleton_table(self) -> None:
        """A one-page comparison may locate a same-box singleton, but cannot confirm its identity."""

        bbox = (147.0, 317.0, 393.0, 373.0)
        old = self._window_table(
            131,
            1,
            bbox,
            "Parameter=Rise Time | Value=35 ps",
        )
        new = self._window_table(
            135,
            1,
            bbox,
            "Parameter=Rise Time | Value=40 ps",
        )

        groups = _paired_table_visuals(
            [old],
            [new],
            single_page_window_pairs={131: 135},
        )

        self.assertEqual(1, len(groups))
        self.assertTrue(groups[0].review_only)
        self.assertEqual("same-position-content-change", groups[0].review_reason)
        self.assertTrue(groups[0].old_tables and groups[0].new_tables)
        changes = _build_table_changes(
            DiffResult(
                old_pdf=Path("old-one-page.pdf"),
                new_pdf=Path("new-one-page.pdf"),
                old_sections=[],
                new_sections=[],
                changes=[],
                warnings=[],
            ),
            table_groups=groups,
        )
        self.assertEqual(["review"], [change.change_type for change in changes])
        self.assertTrue(
            any(
                "35 ps" in row.old_value and "40 ps" in row.new_value
                for row in changes[0].row_changes
            )
        )

    def test_single_page_window_does_not_pair_ambiguous_untitled_tables(self) -> None:
        """Changed identity or multiple candidates must not receive a weak singleton match."""

        bbox = (147.0, 317.0, 393.0, 373.0)
        old = self._window_table(
            131,
            1,
            bbox,
            "Parameter=Rise Time | Value=35 ps",
        )
        changed_identity = self._window_table(
            135,
            1,
            bbox,
            "Parameter=Fall Time | Value=40 ps",
        )
        changed_identity_groups = _paired_table_visuals(
            [old],
            [changed_identity],
            single_page_window_pairs={131: 135},
        )
        self.assertFalse(any(group.review_only for group in changed_identity_groups))

        another_old = self._window_table(
            131,
            2,
            (400.0, 317.0, 500.0, 373.0),
            "Parameter=Fall Time | Value=12 ps",
        )
        another_new = self._window_table(
            135,
            2,
            (400.0, 317.0, 500.0, 373.0),
            "Parameter=Fall Time | Value=14 ps",
        )
        multiple_groups = _paired_table_visuals(
            [old, another_old],
            [changed_identity, another_new],
            single_page_window_pairs={131: 135},
        )
        self.assertFalse(any(group.review_only for group in multiple_groups))

    def test_same_position_candidate_with_unreadable_rows_stays_reviewable(self) -> None:
        """Missing extraction on one side cannot turn a same-box candidate into a deletion/addition."""

        old = self._window_table(
            1518,
            1,
            (40.0, 100.0, 572.0, 700.0),
            "Table 7-258 DPA Register maximum 8 ns",
        )
        new = replace(
            self._window_table(
                1568,
                1,
                old.bbox,
                "unused new-side text",
            ),
            row_texts=[],
        )
        groups = _paired_table_visuals(
            [old],
            [new],
            locator_page_pairs={1518: 1568},
        )

        self.assertEqual(1, len(groups))
        self.assertTrue(groups[0].review_only)
        self.assertEqual("same-position-unreadable-rows", groups[0].review_reason)
        changes = _build_table_changes(
            DiffResult(
                old_pdf=Path("old-window.pdf"),
                new_pdf=Path("new-window.pdf"),
                old_sections=[],
                new_sections=[],
                changes=[],
                warnings=[],
            ),
            table_groups=groups,
        )
        self.assertEqual("review", changes[0].change_type)
        self.assertIn("8 ns", changes[0].row_changes[0].old_value)
        self.assertIn("未提取到", changes[0].row_changes[0].new_value)

    def test_same_position_candidate_with_different_row_count_stays_reviewable(self) -> None:
        """A parser row split is reported as candidate text, not a whole table add/delete."""

        old = self._window_table(
            1518,
            1,
            (40.0, 100.0, 572.0, 700.0),
            "Register Control Enable | 0b",
        )
        new = replace(
            self._window_table(
                1568,
                1,
                old.bbox,
                "Register Control Enable | 1b",
            ),
            row_texts=[
                "Register Control Enable",
                "Changed value | 1b",
            ],
        )
        groups = _paired_table_visuals(
            [old],
            [new],
            locator_page_pairs={1518: 1568},
        )

        self.assertEqual(1, len(groups))
        self.assertTrue(groups[0].review_only)
        self.assertEqual("same-position-content-change", groups[0].review_reason)
        changes = _build_table_changes(
            DiffResult(
                old_pdf=Path("old-window.pdf"),
                new_pdf=Path("new-window.pdf"),
                old_sections=[],
                new_sections=[],
                changes=[],
                warnings=[],
            ),
            table_groups=groups,
        )
        self.assertEqual("review", changes[0].change_type)
        self.assertTrue(any("0b" in row.old_value for row in changes[0].row_changes))
        self.assertTrue(any("1b" in row.new_value for row in changes[0].row_changes))

    def test_ocr_gap_is_bridged_by_neighbors_but_real_page_change_stays_visible(self) -> None:
        """A unique OCR page between mapped neighbors is rendered, not discarded."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "old.pdf"
            new_path = root / "new.pdf"
            old_digest = _write_pages(
                old_path,
                "PCI Express Base Specification Revision 6.4",
                ["Page 10", "Page 11", "Page 12"],
                ["Opening clause", "Identical graph labels", "Closing clause"],
            )
            new_digest = _write_pages(
                new_path,
                "PCI Express Base Specification Revision 6.5",
                ["Page 60", "Page 61", "Page 62"],
                ["Opening clause", "Changed graph labels", "Closing clause"],
            )
            old_pages = [
                _page_text(1, "Opening clause"),
                _page_text(2, "OCR recognized one curve label", ocr_used=True, image_dominant=True),
                _page_text(3, "Closing clause"),
            ]
            new_pages = [
                _page_text(1, "Opening clause"),
                _page_text(2, "OCR recognized another curve label", ocr_used=True, image_dominant=True),
                _page_text(3, "Closing clause"),
            ]

            items, _warnings, audit = detect_visual_review_items(
                _extraction(old_path, old_digest, old_pages),
                _extraction(new_path, new_digest, new_pages),
            )

        self.assertEqual(3, audit.eligible_page_pair_count)
        self.assertEqual(3, audit.checked_page_pair_count)
        self.assertEqual(0, audit.ambiguous_page_count)
        self.assertTrue(audit.complete)
        self.assertEqual(((1, 1), (3, 3)), audit.identical_body_page_pairs)
        self.assertEqual([(2, 2)], [(item.old_page_number, item.new_page_number) for item in items])


if __name__ == "__main__":
    unittest.main()
