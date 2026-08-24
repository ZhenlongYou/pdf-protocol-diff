"""Coordinate-backed running-furniture regression tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import sys  # 单文件和 unittest discovery 都要在导入项目包前定位本地 src。
import tempfile
import unittest
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]  # 从测试文件定位当前 PDF 工具根目录。
SRC_DIR = PROJECT_ROOT / "src"  # 项目采用 src-layout，干净虚拟环境不会自动找到包。
if str(SRC_DIR) not in sys.path:  # 保持已有导入优先级，避免重复路径污染测试顺序。
    sys.path.insert(0, str(SRC_DIR))  # 让标准 discovery 与单文件 PyCharm 运行使用同一源码。

from protocol_pdf_diff.compare import compare_extractions
from protocol_pdf_diff import reporting as reporting_module
from protocol_pdf_diff.models import (
    DiffResult,
    DiffOptions,
    DocumentBlock,
    DocumentBlockKind,
    ExtractionResult,
    PageText,
    Section,
    TableVisual,
)
from protocol_pdf_diff.pdf_extract import (
    _document_proven_line_number_gutter_boxes,
    _extract_pdfplumber_page_text,
    _filtered_layout_page,
    _page_may_contain_table,
)
from protocol_pdf_diff.reporting import write_reports


def _text_block(page_number: int, text: str, top: float) -> DocumentBlock:
    """Build one native line block with explicit page geometry."""

    return DocumentBlock(
        page_number=page_number,
        bbox=(72.0, top, 540.0, top + 12.0),
        kind=DocumentBlockKind.TEXT,
        text=text,
        reading_order=0,
        source_engine="pdfplumber",
    )


def _version_with_running_footer(name: str, revision: str) -> ExtractionResult:
    """Return three pages whose bottom footer is interleaved into body text."""

    title = f"Protocol Working Group - Clause 8: Link Requirements {revision}"
    copyright_line = f"Copyright © {revision} Protocol Working Group"
    draft_line = "This is a draft and not to be shared before publication approval."
    pages: list[PageText] = []
    for page_number in range(1, 4):
        heading = f"{page_number} Requirement {page_number}"
        body = "The receiver shall preserve every declared operating limit."
        continuation = "The implementation shall retain traceable review evidence."
        footer_title = (
            f"{page_number} {title}"
            if page_number % 2
            else f"{title} {page_number}"
        )
        pages.append(
            PageText(
                page_number=page_number,
                text="\n".join(
                    [
                        heading,
                        body,
                        f"{continuation} {footer_title}",
                        copyright_line,
                        draft_line,
                    ]
                ),
                blocks=(
                    _text_block(page_number, heading, 90.0),
                    _text_block(page_number, body, 150.0),
                    _text_block(page_number, continuation, 300.0),
                    _text_block(page_number, footer_title, 718.0),
                    _text_block(page_number, copyright_line, 744.0),
                    _text_block(page_number, draft_line, 756.0),
                ),
                page_bbox=(0.0, 0.0, 612.0, 792.0),
            )
        )
    return ExtractionResult(pdf_path=Path(name), pages=pages, total_pages=3)


class CoordinatePageFurnitureTests(unittest.TestCase):
    def test_revision_table_with_nine_rectangles_passes_narrow_table_gate(self) -> None:
        """A lightly ruled revision record is found without lowering the global gate."""

        page = mock.Mock()
        page.lines = []
        page.rects = [{} for _ in range(9)]
        revision_text = (
            "Changes from the previous revision are listed in the table below.\n"
            "Revision Date Description"
        )

        self.assertTrue(_page_may_contain_table(page, revision_text))
        self.assertFalse(
            _page_may_contain_table(
                page,
                "The values described below are ordinary body prose.",
            )
        )
        self.assertFalse(
            _page_may_contain_table(
                page,
                "The table below contains Revision and Date fields.",
            )
        )

    def test_revision_table_gate_accepts_common_intro_variants_only_with_full_schema(self) -> None:
        """Revision records use several captions, but still need geometry and all fields."""

        page = mock.Mock()
        page.lines = []
        page.rects = [{} for _ in range(7)]
        for introduction in (
            "Changes are listed in the following table.",
            "Revision History",
            "Change log",
        ):
            with self.subTest(introduction=introduction):
                self.assertTrue(
                    _page_may_contain_table(
                        page,
                        f"{introduction}\nRevision Date Description",
                    )
                )
                self.assertFalse(
                    _page_may_contain_table(
                        page,
                        f"{introduction}\nRevision Date",
                    )
                )

    """Use public comparison behavior to verify coordinate-backed cleanup."""

    def test_repeated_bottom_footer_is_removed_even_when_text_order_interleaves_it(self) -> None:
        """Bottom geometry must beat misleading PDF text-stream order."""

        result = compare_extractions(
            _version_with_running_footer("old.pdf", "2025"),
            _version_with_running_footer("new.pdf", "2026"),
            DiffOptions(),
        )

        self.assertEqual([], result.changes)

    def test_coordinate_proven_pcie_multiline_revision_footer_is_removed(self) -> None:
        """PCIe title, revision, date, and dynamic page number are one footer cluster."""

        def version(name: str, page_start: int, revision: str, date: str) -> ExtractionResult:
            pages: list[PageText] = []
            for logical_index, page_number in enumerate(range(page_start, page_start + 3), start=1):
                heading = f"2.{logical_index} Receiver Requirement {logical_index}"
                body = "The receiver shall preserve the declared calibration waveform."
                title = f"PCI Express Architecture PHY Test Specification | {page_number}"
                revision_line = f"Revision {revision}"
                pages.append(
                    PageText(
                        page_number=page_number,
                        text="\n".join((heading, body, title, revision_line, date)),
                        blocks=(
                            _text_block(page_number, heading, 90.0),
                            _text_block(page_number, body, 300.0),
                            _text_block(page_number, title, 721.0),
                            _text_block(page_number, revision_line, 734.0),
                            _text_block(page_number, date, 746.0),
                        ),
                        page_bbox=(0.0, 0.0, 612.0, 792.0),
                    )
                )
            return ExtractionResult(
                pdf_path=Path(name),
                pages=pages,
                total_pages=80,
                selected_start_page=page_start,
                selected_end_page=page_start + 2,
            )

        result = compare_extractions(
            version("old-pcie-footer.pdf", 16, "3.0", "June 6, 2013"),
            version("new-pcie-footer.pdf", 33, "4.0, Version 1.2", "August 18, 2021"),
            DiffOptions(),
        )

        self.assertEqual([], result.changes)

    def test_bottom_requirement_shaped_like_a_specification_title_is_preserved(self) -> None:
        """Bottom geometry cannot delete a repeated normative requirement."""

        def version(name: str, selected_value: int) -> ExtractionResult:
            pages: list[PageText] = []
            for page_number in range(1, 4):
                heading = f"{page_number} Requirement {page_number}"
                body = "The receiver shall preserve the declared calibration waveform."
                requirement = f"Receiver shall use Test Specification | {selected_value}"
                pages.append(
                    PageText(
                        page_number=page_number,
                        text="\n".join((heading, body, requirement)),
                        blocks=(
                            _text_block(page_number, heading, 90.0),
                            _text_block(page_number, body, 300.0),
                            _text_block(page_number, requirement, 721.0),
                        ),
                        page_bbox=(0.0, 0.0, 612.0, 792.0),
                    )
                )
            return ExtractionResult(pdf_path=Path(name), pages=pages, total_pages=3)

        result = compare_extractions(
            version("old-bottom-requirement.pdf", 3),
            version("new-bottom-requirement.pdf", 4),
            DiffOptions(),
        )

        self.assertTrue(result.changes)
        changed_text = "\n".join(
            [
                *(pair.old for change in result.changes for pair in change.replaced_snippets),
                *(pair.new for change in result.changes for pair in change.replaced_snippets),
            ]
        )
        self.assertIn("Test Specification | 3", changed_text)
        self.assertIn("Test Specification | 4", changed_text)

    def test_running_title_is_separate_and_changed_value_remains_auditable(self) -> None:
        """Header evidence avoids body noise without hiding a changed identifier."""

        def version(name: str, revision: str, page_count: int) -> ExtractionResult:
            pages = []
            for page_number in range(1, page_count + 1):
                displayed_revision = (
                    revision.zfill(4) if page_number % 2 == 0 else revision
                )
                title = (
                    "Implementation Agreement Protocol Specification "
                    f"IA-{displayed_revision}"
                )
                heading = f"{page_number} Requirement {page_number}"
                body = "The receiver shall preserve every declared operating limit."
                has_document_proof = page_count >= 3
                pages.append(
                    PageText(
                        page_number=page_number,
                        text=(
                            "\n".join((heading, body))
                            if has_document_proof
                            else "\n".join((title, heading, body))
                        ),
                        blocks=(
                            _text_block(page_number, title, 30.0),
                            _text_block(page_number, heading, 90.0),
                            _text_block(page_number, body, 300.0),
                        ),
                        page_bbox=(0.0, 0.0, 612.0, 792.0),
                        running_header_texts=(title,) if has_document_proof else (),
                    )
                )
            return ExtractionResult(pdf_path=Path(name), pages=pages, total_pages=page_count)

        repeated = compare_extractions(
            version("old.pdf", "6.0", 4),
            version("new.pdf", "7.0", 4),
            DiffOptions(),
        )
        repeated_unchanged = compare_extractions(
            version("old.pdf", "6.0", 4),
            version("new.pdf", "6.0", 4),
            DiffOptions(),
        )
        single_page = compare_extractions(
            version("old.pdf", "A", 1),
            version("new.pdf", "B", 1),
            DiffOptions(),
        )

        self.assertEqual(1, len(repeated.changes))
        self.assertEqual("运行页眉（坐标证据）", repeated.changes[0].report_location)
        self.assertIn("IA-6.0", repeated.changes[0].replaced_snippets[0].old)
        self.assertIn("IA-7.0", repeated.changes[0].replaced_snippets[0].new)
        self.assertEqual([], repeated_unchanged.changes)
        self.assertTrue(single_page.changes)

    def test_running_header_case_only_change_is_reader_equivalent(self) -> None:
        """Publication casing in a running header is furniture, not a technical change."""

        def version(name: str, running_header: str) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Receiver Requirement\nThe receiver shall preserve calibration.",
                        running_header_texts=(running_header,),
                    )
                ],
                total_pages=1,
            )

        result = compare_extractions(
            version("old-header-case.pdf", "TEST DESCRIPTIONS"),
            version("new-header-case.pdf", "Test Descriptions"),
            DiffOptions(),
        )

        self.assertEqual([], result.changes)

    def test_uniform_running_header_identifier_case_change_remains_visible(self) -> None:
        """A fixed running header can still carry a case-sensitive identifier."""

        def version(name: str, running_header: str) -> ExtractionResult:
            return ExtractionResult(
                pdf_path=Path(name),
                pages=[
                    PageText(
                        page_number=1,
                        text="1 Receiver Requirement\nThe receiver shall preserve calibration.",
                        running_header_texts=(running_header,),
                    )
                ],
                total_pages=1,
            )

        for old_header, new_header in (
            ("MODE_FAST", "mode_fast"),
            ("RX_CAL", "rx_cal"),
            ("Consortium Protocol ID ALPHA", "Consortium Protocol ID alpha"),
            ("GT/S LIMIT", "gt/s limit"),
        ):
            with self.subTest(old_header=old_header):
                result = compare_extractions(
                    version("old-header-identifier.pdf", old_header),
                    version("new-header-identifier.pdf", new_header),
                    DiffOptions(),
                )

                self.assertEqual(1, len(result.changes))
                self.assertEqual(
                    "运行页眉（坐标证据）",
                    result.changes[0].report_location,
                )

    def test_repeated_body_requirement_is_not_removed_as_page_furniture(self) -> None:
        """Repetition alone cannot hide a technical change away from the margin."""

        def version(name: str, state: str) -> ExtractionResult:
            pages: list[PageText] = []
            for page_number in range(1, 4):
                heading = f"{page_number} Requirement {page_number}"
                requirement = f"Operating mode shall remain {state}."
                pages.append(
                    PageText(
                        page_number=page_number,
                        text=f"{heading}\n{requirement}",
                        blocks=(
                            _text_block(page_number, heading, 90.0),
                            _text_block(page_number, requirement, 320.0),
                        ),
                        page_bbox=(0.0, 0.0, 612.0, 792.0),
                    )
                )
            return ExtractionResult(pdf_path=Path(name), pages=pages, total_pages=3)

        result = compare_extractions(
            version("old.pdf", "Disabled"),
            version("new.pdf", "Enabled"),
            DiffOptions(),
        )
        changed_text = "\n".join(
            [
                *(pair.old for change in result.changes for pair in change.replaced_snippets),
                *(pair.new for change in result.changes for pair in change.replaced_snippets),
                *(text for change in result.changes for text in change.removed_snippets),
                *(text for change in result.changes for text in change.added_snippets),
            ]
        )

        self.assertIn("Disabled", changed_text)
        self.assertIn("Enabled", changed_text)

    def test_duplicate_margin_fragments_on_one_page_do_not_fake_repetition(self) -> None:
        """Two variants on one page are still only one page of evidence."""

        def version(name: str, revision: str) -> ExtractionResult:
            pages: list[PageText] = []
            title = f"Protocol Working Group - Clause 8: Review Copy {revision}"
            for page_number in range(1, 4):
                heading = f"{page_number} Requirement {page_number}"
                body_lines = [heading, "The receiver shall preserve the declared limit."]
                blocks = [
                    _text_block(page_number, heading, 90.0),
                    _text_block(page_number, body_lines[1], 300.0),
                ]
                if page_number == 1:
                    leading = f"1 {title}"
                    trailing = f"{title} 1"
                    body_lines.extend([leading, trailing])
                    blocks.extend(
                        [
                            _text_block(page_number, leading, 718.0),
                            _text_block(page_number, trailing, 744.0),
                        ]
                    )
                pages.append(
                    PageText(
                        page_number=page_number,
                        text="\n".join(body_lines),
                        blocks=tuple(blocks),
                        page_bbox=(0.0, 0.0, 612.0, 792.0),
                    )
                )
            return ExtractionResult(pdf_path=Path(name), pages=pages, total_pages=3)

        result = compare_extractions(
            version("old.pdf", "A"),
            version("new.pdf", "B"),
            DiffOptions(),
        )
        changed_text = "\n".join(
            [
                *(pair.old for change in result.changes for pair in change.replaced_snippets),
                *(pair.new for change in result.changes for pair in change.replaced_snippets),
                *(text for change in result.changes for text in change.removed_snippets),
                *(text for change in result.changes for text in change.added_snippets),
            ]
        )

        self.assertIn("Review Copy A", changed_text)
        self.assertIn("Review Copy B", changed_text)

    def test_repeated_body_url_near_text_bottom_is_not_a_page_footer(self) -> None:
        """The block envelope cannot substitute for the actual page boundary."""

        def version(name: str, profile: str) -> ExtractionResult:
            pages: list[PageText] = []
            for page_number in range(1, 4):
                heading = f"{page_number} Requirement {page_number}"
                requirement = (
                    "The receiver shall use "
                    f"https://example.test/{profile} for calibration."
                )
                pages.append(
                    PageText(
                        page_number=page_number,
                        text=f"{heading}\n{requirement}",
                        blocks=(
                            _text_block(page_number, heading, 90.0),
                            _text_block(page_number, requirement, 600.0),
                        ),
                        page_bbox=(0.0, 0.0, 612.0, 792.0),
                    )
                )
            return ExtractionResult(pdf_path=Path(name), pages=pages, total_pages=3)

        result = compare_extractions(
            version("old.pdf", "required-profile"),
            version("new.pdf", "revised-profile"),
            DiffOptions(),
        )
        changed_text = "\n".join(
            pair.old + "\n" + pair.new
            for change in result.changes
            for pair in change.replaced_snippets
        )

        self.assertIn("required-profile", changed_text)
        self.assertIn("revised-profile", changed_text)

    def test_body_reference_matching_a_footer_fragment_is_preserved(self) -> None:
        """Geometry proves one footer instance, not every equal page-text substring."""

        footer = "Copyright © 2026 Protocol Working Group"

        def version(name: str, include_reference: bool) -> ExtractionResult:
            pages: list[PageText] = []
            for page_number in range(1, 4):
                heading = f"{page_number} Requirement {page_number}"
                requirement = (
                    f"The requirement references {footer} for attribution."
                    if include_reference
                    else "The requirement references no external attribution."
                )
                pages.append(
                    PageText(
                        page_number=page_number,
                        text="\n".join([heading, requirement, footer]),
                        blocks=(
                            _text_block(page_number, heading, 90.0),
                            _text_block(page_number, requirement, 300.0),
                            _text_block(page_number, footer, 744.0),
                        ),
                        page_bbox=(0.0, 0.0, 612.0, 792.0),
                    )
                )
            return ExtractionResult(pdf_path=Path(name), pages=pages, total_pages=3)

        result = compare_extractions(
            version("old.pdf", True),
            version("new.pdf", False),
            DiffOptions(),
        )
        changed_text = "\n".join(
            [
                *(pair.old for change in result.changes for pair in change.replaced_snippets),
                *(text for change in result.changes for text in change.removed_snippets),
            ]
        )

        self.assertIn(footer, changed_text)

    def test_footer_box_does_not_start_at_an_unrelated_body_url(self) -> None:
        """Only a URL belonging to the footer marker cluster may anchor its box."""

        page = mock.Mock()
        page.width = 612
        page.height = 792
        page.chars = []
        page.crop.return_value = page
        page.extract_words.return_value = [
            {"text": "https://standards.example/limit", "x0": 72, "x1": 240, "top": 680, "bottom": 691},
            {"text": "www.publisher.example", "x0": 72, "x1": 180, "top": 733, "bottom": 744},
            {"text": "Copyright", "x0": 72, "x1": 121, "top": 749, "bottom": 760},
            {"text": "draft", "x0": 126, "x1": 151, "top": 760, "bottom": 771},
        ]
        filtered = object()
        page.filter.return_value = filtered

        self.assertIs(filtered, _filtered_layout_page(page))
        predicate = page.filter.call_args.args[0]

        self.assertTrue(
            predicate(
                {"text": "h", "x0": 73, "x1": 78, "top": 681, "bottom": 690}
            )
        )
        self.assertFalse(
            predicate(
                {"text": "w", "x0": 73, "x1": 78, "top": 734, "bottom": 743}
            )
        )

    def test_single_page_aligned_numbered_requirements_are_not_removed_as_a_gutter(self) -> None:
        """One page alone cannot distinguish a long list from a resetting print gutter."""

        page = mock.Mock()  # 用纯坐标反例复现正文编号恰好靠左且纵向对齐的常见排版。
        page.width = 612.0  # 沿用 OIF 页面宽度，使编号落入行号候选的旧 16% 区域。
        page.height = 1000.0  # 40 个条目覆盖足够高度，超过任何固定数量门槛仍必须保守保留。
        page.chars = []  # 排除水印证据，让结果只由编号列表与行号栏判定决定。
        numbered_requirements: list[dict[str, object]] = []  # 每项编号和正文共享基线，表示真实列表结构。
        for value in range(1, 41):
            top = 90.0 + (value - 1) * 20.0  # 合法列表可以规则排版，不能仅凭等距就删除编号。
            numbered_requirements.extend(
                (
                    {
                        "text": str(value),
                        "x0": 53.0 if value < 10 else 47.0,
                        "x1": 59.0,
                        "top": top,
                        "bottom": top + 12.0,
                    },
                    {
                        "text": f"Requirement{value}",
                        "x0": 72.0,
                        "x1": 170.0,
                        "top": top,
                        "bottom": top + 12.0,
                    },
                )
            )
        page.extract_words.return_value = numbered_requirements  # 生产入口仍获取完整坐标词集合。

        result = _filtered_layout_page(
            page,
            coordinate_words=numbered_requirements,
            footer_boxes=(),
            header_boxes=(),
        )  # 没有可区分于正文列表的强证据时，过滤器必须 fail-open 保留原页。

        self.assertIs(page, result)  # 返回原页才能保证 1..40 全部进入后续文本比较。
        page.filter.assert_not_called()  # 任何字符级过滤都会给合法编号带来静默丢失风险。
        self.assertEqual(
            {},
            _document_proven_line_number_gutter_boxes(
                [(1, page)],
                {1: numbered_requirements},
            ),
        )  # 固定阈值再高也不能把单页歧义升级成“已证明”。

    def test_repeated_resetting_body_lists_keep_a_30_to_31_change(self) -> None:
        """Cross-page repetition cannot authorize deleting an aligned numbered body list."""

        def numbered_body_words(
            final_value: int,
            *,
            body_top_offset: float,
        ) -> list[dict[str, object]]:
            values = [*range(1, 30), final_value]  # 独立写出边界变化：旧页末项 30，新页末项 31。
            words: list[dict[str, object]] = []
            for row, value in enumerate(values):
                top = 80.0 + row * 20.0
                words.extend(
                    (
                        {
                            "text": str(value),
                            "x0": 53.0 if value < 10 else 47.0,
                            "x1": 59.0,
                            "top": top,
                            "bottom": top + 12.0,
                        },
                        {
                            "text": f"Requirement{row + 1}",
                            "x0": 72.0,
                            "x1": 230.0,
                            "top": top + body_top_offset,
                            "bottom": top + body_top_offset + 12.0,
                        },
                    )
                )
            return words

        def document(
            final_value_on_page_two: int,
            *,
            body_top_offset: float,
        ) -> tuple[
            list[tuple[int, object]],
            dict[int, list[dict[str, object]]],
        ]:
            pages: list[tuple[int, object]] = []
            words_by_page: dict[int, list[dict[str, object]]] = {}
            for page_number, final_value in ((1, 30), (2, final_value_on_page_two)):
                page = mock.Mock()
                page.width = 612.0
                page.height = 792.0
                page.chars = []
                page_words = numbered_body_words(
                    final_value,
                    body_top_offset=body_top_offset,
                )
                page.extract_words.return_value = page_words
                pages.append((page_number, page))
                words_by_page[page_number] = page_words
            return pages, words_by_page

        for final_value, body_top_offset in ((30, 0.0), (31, 0.0), (31, 4.0)):
            with self.subTest(
                final_value=final_value,
                body_top_offset=body_top_offset,
            ):
                pages, words_by_page = document(
                    final_value,
                    body_top_offset=body_top_offset,
                )
                gutter_boxes = _document_proven_line_number_gutter_boxes(
                    pages,
                    words_by_page,
                )

                self.assertEqual({}, gutter_boxes)  # 两页重复只证明排版相似，不能证明数字属于可删除页边栏。
                for page_number, page in pages:
                    self.assertIs(
                        page,
                        _filtered_layout_page(
                            page,
                            coordinate_words=words_by_page[page_number],
                            gutter_boxes=gutter_boxes.get(page_number, ()),
                            footer_boxes=(),
                            header_boxes=(),
                        ),
                    )
                    page.filter.assert_not_called()  # 30/31 都必须保留给 sectioning 和正文差异识别。

    def test_three_page_fixed_1_to_49_print_grid_is_filtered_from_comparison_only(self) -> None:
        """A repeated full-page print grid with blank numbered rows is not a body list."""

        pages: list[tuple[int, object]] = []
        words_by_page: dict[int, list[dict[str, object]]] = {}
        semantic_edge_words: dict[int, dict[str, object]] = {}
        for page_number in range(1, 4):
            page = mock.Mock()
            page.width = 612.0
            page.height = 792.0
            page.bbox = (0.0, 0.0, 612.0, 792.0)
            page.chars = []
            side = "right" if page_number % 2 else "left"
            page_words: list[dict[str, object]] = []
            for value in range(1, 50):
                top = 78.0 + (value - 1) * 13.0
                if side == "left":
                    x0, x1 = (53.0, 60.0) if value < 10 else (47.0, 60.0)
                else:
                    x0, x1 = (555.0, 562.0) if value < 10 else (555.0, 568.0)
                page_words.append(
                    {
                        "text": str(value),
                        "x0": x0,
                        "x1": x1,
                        "top": top,
                        "bottom": top + 11.0,
                    }
                )
                if value % 4:  # 每四个打印行保留一个无正文基线，证明它给空白视觉行也编号。
                    page_words.append(
                        {
                            "text": f"Body{value}",
                            "x0": 72.0,
                            "x1": 150.0,
                            "top": top,
                            "bottom": top + 11.0,
                        }
                    )
            semantic_edge_word = {
                "text": "7",
                "x0": 50.0 if side == "left" else 560.0,
                "x1": 55.0 if side == "left" else 565.0,
                "top": 250.0,
                "bottom": 261.0,
            }
            page_words.append(semantic_edge_word)  # 同一边带内的独立技术数值不参与 1..49 主网格证明。
            semantic_edge_words[page_number] = semantic_edge_word
            page.extract_words.return_value = page_words
            pages.append((page_number, page))
            words_by_page[page_number] = page_words

        gutter_boxes = _document_proven_line_number_gutter_boxes(
            pages,
            words_by_page,
        )

        self.assertEqual({1, 2, 3}, set(gutter_boxes))  # 至少三页完整重置网格才能获得跨页证明。
        self.assertEqual(
            {},
            _document_proven_line_number_gutter_boxes(
                pages,
                words_by_page,
                coordinate_issues_by_page={
                    1: (["同基线正文坐标无效并被忽略"], None),
                },
            ),
        )  # 三页样本中任一页坐标不完整，剩余两页不得借假 orphan 获得删除权。
        benign_duplicate_boxes = _document_proven_line_number_gutter_boxes(
            pages,
            words_by_page,
            coordinate_issues_by_page={
                1: (["第 1 页跳过重复坐标词。"], None),
            },
        )
        self.assertEqual(
            {1, 2, 3},
            set(benign_duplicate_boxes),
        )  # 少量重复词已被去重且网格仍完整时，不应让该页独自泄漏全部 1..49 行号。
        for page_number, page in pages:
            self.assertEqual(49, len(gutter_boxes[page_number]))  # 每个已证明网格词都有独立精确 bbox，不返回整条带。
            filtered_page = mock.Mock()
            filtered_page.width = page.width
            filtered_page.height = page.height
            filtered_page.bbox = page.bbox
            filtered_page.extract_text.return_value = "32 Interface Requirements"
            page.filter.return_value = filtered_page
            filtered = _filtered_layout_page(
                page,
                coordinate_words=words_by_page[page_number],
                gutter_boxes=gutter_boxes[page_number],
                footer_boxes=(),
                header_boxes=(),
            )
            self.assertIs(filtered_page, filtered)
            predicate = page.filter.call_args.args[0]
            self.assertFalse(predicate(words_by_page[page_number][0]))  # 只剥离窄边列里的数字字形。
            body_word = next(
                word
                for word in words_by_page[page_number]
                if str(word["text"]).startswith("Body")
            )
            self.assertTrue(predicate(body_word))  # 正文和靠近页边的其他数值都不得被带走。
            self.assertTrue(
                predicate(semantic_edge_words[page_number])
            )  # 即使独立技术 7 落在同一页边带，不在已证明词 bbox 内就必须保留。

        first_page = pages[0][1]
        first_page.filter.return_value.extract_text.return_value = "32 Interface Requirements"
        with (
            mock.patch(
                "protocol_pdf_diff.pdf_extract._assess_page_reading_order",
                return_value=(False, None, False, 120),
            ),
            mock.patch(
                "protocol_pdf_diff.pdf_extract._extract_scan_page_text_with_evidence",
                return_value=("32 Interface Requirements", [], False, False, None),
            ),
            mock.patch(
                "protocol_pdf_diff.pdf_extract._page_may_contain_table",
                return_value=False,
            ),
        ):
            extracted_text, warnings, _visuals, layout_risk, *_flags, blocks = (
                _extract_pdfplumber_page_text(
                    first_page,
                    "printed-line-numbers.pdf",
                    1,
                    coordinate_evidence=(words_by_page[1], [], None),
                    gutter_boxes=gutter_boxes[1],
                )
            )

        self.assertEqual("32 Interface Requirements", extracted_text)
        self.assertFalse(layout_risk)  # 已证明并过滤的行号不再是未解决的布局歧义。
        self.assertIn("已证明并从比较文本过滤", "\n".join(warnings))
        self.assertNotIn("已保留全部数字", "\n".join(warnings))
        self.assertTrue(
            any(block.text.strip() == "4" for block in blocks)
        )  # 比较面清理前已建立的原始坐标块仍保留行号审计事实。

    def test_three_page_complete_numbered_body_lists_remain_fail_open(self) -> None:
        """Blank numbered baselines, not repetition alone, distinguish a print grid."""

        pages: list[tuple[int, object]] = []
        words_by_page: dict[int, list[dict[str, object]]] = {}
        for page_number in range(1, 4):
            page = mock.Mock()
            page.width = 612.0
            page.height = 792.0
            page.chars = []
            page_words: list[dict[str, object]] = []
            for value in range(1, 50):
                top = 78.0 + (value - 1) * 13.0
                page_words.extend(
                    (
                        {
                            "text": str(value),
                            "x0": 53.0 if value < 10 else 47.0,
                            "x1": 60.0,
                            "top": top,
                            "bottom": top + 11.0,
                        },
                        {
                            "text": f"Requirement{value}",
                            "x0": 72.0,
                            "x1": 190.0,
                            "top": top,
                            "bottom": top + 11.0,
                        },
                    )
                )  # 每个序号都有同基线内容，即使重复三页也仍是可能的合法正文列表。
            pages.append((page_number, page))
            words_by_page[page_number] = page_words

        self.assertEqual(
            {},
            _document_proven_line_number_gutter_boxes(pages, words_by_page),
        )  # 无空白编号基线时必须 fail-open，不能被“三页 + 1..49”门槛误删。

    def test_preserved_gutter_like_numbers_mark_the_page_as_layout_risk(self) -> None:
        """保留疑似行号后必须降级，不能把歧义页面伪装成可靠正文。"""

        page = mock.Mock()
        page.width = 612.0
        page.height = 792.0
        page.bbox = (0.0, 0.0, 612.0, 792.0)
        page.chars = []
        coordinate_words = [
            {
                "text": str(value),
                "x0": 53.0 if value < 10 else 47.0,
                "x1": 59.0,
                "top": 80.0 + (value - 1) * 20.0,
                "bottom": 92.0 + (value - 1) * 20.0,
            }
            for value in range(1, 31)
        ]
        page_text = "\n".join(str(value) for value in range(1, 31))
        page.extract_text.return_value = page_text

        with (
            mock.patch(
                "protocol_pdf_diff.pdf_extract._assess_page_reading_order",
                return_value=(False, None, False, len(page_text)),
            ),
            mock.patch(
                "protocol_pdf_diff.pdf_extract._extract_scan_page_text_with_evidence",
                return_value=(page_text, [], False, False, None),
            ),
            mock.patch(
                "protocol_pdf_diff.pdf_extract._page_may_contain_table",
                return_value=False,
            ),
        ):
            extracted_text, warnings, _visuals, layout_risk, *_rest = (
                _extract_pdfplumber_page_text(
                    page,
                    "numbered-list.pdf",
                    1,
                    coordinate_evidence=(coordinate_words, [], None),
                    gutter_boxes=(),
                )
            )

        self.assertEqual(page_text, extracted_text)  # 可疑数字列完整进入真实页抽取返回值。
        self.assertTrue(layout_risk)  # 风险状态而非删除承担不确定性。
        self.assertIn("已保留全部数字", "\n".join(warnings))
        page.filter.assert_not_called()

    def test_repeated_gutter_like_column_is_preserved_without_semantic_coordinates(self) -> None:
        """即使重复数字列很像行号，缺少语义坐标时也必须整体保留。"""

        page = mock.Mock()  # 用坐标可控的页面隔离 gutter 判定，不依赖某个出版方的 PDF 导出器。
        page.width = 612.0  # 采用真实 OIF 常见页面宽度，使 16% 旧边界覆盖到正文起点。
        page.height = 792.0  # 页面高度让 40 个真实行号候选覆盖显著垂直范围。
        page.chars = []  # 本反例不含水印，只验证行号列与正文数字的空间分离。
        gutter_words = [  # 1..40 右对齐到 x=59；故意缺 10，复现 DRAFT 字形把该词并成 T10。
            {
                "text": str(value),
                "x0": 53.0 if value < 10 else 47.0,
                "x1": 59.0,
                "top": 80.0 + value * 13.0,
                "bottom": 92.0 + value * 13.0,
            }
            for value in range(1, 41)
            if value != 10  # 其余连续段仍足以证明列，过滤窄带应恢复对第 10 行号字形的覆盖。
        ]
        body_words = [  # 这些数字均在旧版左侧 16% 范围内，但明显不属于 x=59 的行号列。
            {"text": "100", "x0": 72.0, "x1": 92.0, "top": 360.0, "bottom": 372.0},
            {"text": "32", "x0": 72.0, "x1": 85.0, "top": 120.0, "bottom": 132.0},
            {"text": "2", "x0": 80.0, "x1": 87.0, "top": 240.0, "bottom": 252.0},
            {"text": "1", "x0": 80.0, "x1": 87.0, "top": 520.0, "bottom": 532.0},
        ]
        page.extract_words.return_value = [*gutter_words, *body_words]  # 复现“真实序列 + 邻近正文数字”的混合候选集合。
        next_page = mock.Mock()  # 第二页在同一内侧边缘重新从 1 编号，提供独立的打印行号证据。
        next_page.width = page.width
        next_page.height = page.height
        repeated_gutter_words = [dict(word) for word in gutter_words]
        gutter_boxes = _document_proven_line_number_gutter_boxes(
            [(1, page), (2, next_page)],
            {1: [*gutter_words, *body_words], 2: repeated_gutter_words},
        )

        self.assertEqual({}, gutter_boxes)  # 跨页重复不能证明数字是装饰性行号。
        self.assertIs(
            page,
            _filtered_layout_page(
                page,
                coordinate_words=[*gutter_words, *body_words],
                gutter_boxes=gutter_boxes.get(1, ()),
                footer_boxes=(),
                header_boxes=(),
            ),
        )
        page.filter.assert_not_called()  # 行号状数字、缺失的 10 和附近正文数值都不能被静默裁掉。

    def test_same_caption_table_pairs_across_redundant_parent_section_prefix(self) -> None:
        """A newly recognized top-level parent must not turn one table into add/delete."""

        body = "The channel model shall preserve every declared parameter. " * 8
        old = ExtractionResult(
            pdf_path=Path("old.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "32.2 General Requirements\n"
                        "32.2.4 Channel Compliance\n"
                        f"32.2.4.1 Reference Model\n{body}"
                    ),
                )
            ],
            table_visuals=[
                TableVisual(
                    page_number=1,
                    table_number=1,
                    title="Table 32-1. COM Parameter Values",
                    bbox=(72.0, 200.0, 540.0, 500.0),
                    image_data_uri="",
                    row_texts=["表格行: T1 | Parameter=Resistance | Value=50 Ω"],
                    grid_summary="structured rows",
                )
            ],
        )
        new = ExtractionResult(
            pdf_path=Path("new.pdf"),
            pages=[
                PageText(
                    page_number=1,
                    text=(
                        "32 Main Interface\n"
                        "32.2 General Requirements\n"
                        "32.2.4 Channel Compliance\n"
                        f"32.2.4.1 Reference Model\n{body}"
                    ),
                )
            ],
            table_visuals=[
                TableVisual(
                    page_number=1,
                    table_number=1,
                    title="Table 32-1. COM Parameter Values",
                    bbox=(72.0, 200.0, 540.0, 500.0),
                    image_data_uri="",
                    row_texts=["表格行: T1 | Parameter=Resistance | Value=46.25 Ω"],
                    grid_summary="structured rows",
                )
            ],
        )
        result = compare_extractions(old, new, DiffOptions())

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertEqual(1, len(payload["table_changes"]))
        self.assertEqual("modified", payload["table_changes"][0]["change_type"])

    def test_adjacent_same_caption_pages_stay_one_table_across_section_boundary(self) -> None:
        """A continued named table may cross into a newly detected section context."""

        sections = [
            Section(
                section_id="S1",
                heading="32.2.4.2 Channel Operating Margin",
                title="Channel Operating Margin",
                level=4,
                heading_path=("32.2.4.2 Channel Operating Margin",),
                number_path=("32.2.4.2",),
                start_page=1,
                end_page=1,
                body="body",
            ),
            Section(
                section_id="S2",
                heading="32.2.4.3 Informative Channel Insertion Loss",
                title="Informative Channel Insertion Loss",
                level=4,
                heading_path=("32.2.4.3 Informative Channel Insertion Loss",),
                number_path=("32.2.4.3",),
                start_page=2,
                end_page=2,
                body="body",
            ),
        ]
        title = "Table 32-1. COM Parameter Values"
        old_tables = [
            TableVisual(
                1,
                1,
                title,
                (0, 0, 100, 100),
                "",
                ["表格行: T1 | Parameter=A | Value=1"],
                "rows",
            ),
            TableVisual(
                2,
                1,
                title,
                (0, 0, 100, 100),
                "",
                ["表格行: T1 | Parameter=B | Value=2"],
                "rows",
            ),
        ]
        new_tables = [
            TableVisual(
                3,
                1,
                title,
                (0, 0, 100, 100),
                "",
                ["表格行: T1 | Parameter=A | Value=1"],
                "rows",
            ),
            TableVisual(
                4,
                1,
                title,
                (0, 0, 100, 100),
                "",
                ["表格行: T1 | Parameter=B | Value=3"],
                "rows",
            ),
        ]
        new_sections = [
            replace(
                section,
                start_page=section.start_page + 2,
                end_page=section.end_page + 2,
            )
            for section in sections
        ]

        groups = reporting_module._paired_table_visuals(
            old_tables,
            new_tables,
            old_sections=sections,
            new_sections=new_sections,
        )

        self.assertEqual(1, len(groups))
        self.assertEqual(2, len(groups[0].old_tables))
        self.assertEqual(2, len(groups[0].new_tables))

    def test_adjacent_same_caption_tables_in_unrelated_sections_stay_separate(self) -> None:
        """Adjacency alone cannot merge independently reused table captions."""

        sections = [
            Section("S1", "1 First", "First", 1, ("1 First",), ("1",), 1, 1, "body"),
            Section("S2", "2 Second", "Second", 1, ("2 Second",), ("2",), 2, 2, "body"),
        ]
        tables = [
            TableVisual(1, 1, "Table 1 Shared limits", (0, 0, 1, 1), "", ["A"], "rows"),
            TableVisual(2, 1, "Table 1 Shared limits", (0, 0, 1, 1), "", ["B"], "rows"),
        ]

        grouped = reporting_module._table_visual_indexes_by_caption_key(tables, sections)

        self.assertEqual(2, len(grouped))

    def test_untitled_table_in_a_new_section_is_not_absorbed_into_previous_run(self) -> None:
        """``is_continuation`` alone cannot merge an independent table across chapters."""

        caption = "Table 1. Shared Limits"  # 旧版第一页的有题表 A 提供当前逻辑 run。
        old_a = TableVisual(  # 表 A 只含发送端事实。
            1, 1, caption, (0, 0, 100, 100), "",
            ["表格行: T1 | Parameter=A | Value=1"], "rows",
        )
        old_b = TableVisual(  # 页 2 的独立表 B 无题，抽取层只能给出弱 continuation 标志。
            2, 1, "", (0, 0, 100, 100), "",
            ["表格行: T1 | Parameter=B | Value=2"], "rows",
            is_continuation=True,
        )
        new_combined = TableVisual(  # 新版把 A、B 两行真实合入一张有题表。
            1, 1, caption, (0, 0, 100, 100), "",
            [
                "表格行: T1 | Parameter=A | Value=1",
                "表格行: T1 | Parameter=B | Value=2",
            ],
            "rows",
        )
        old_sections = [  # 章节切换证明旧页 2 不是表 A 的自然续页。
            Section("S1", "1 Transmitter", "Transmitter", 1, ("1 Transmitter",), ("1",), 1, 1, "body"),
            Section("S2", "2 Receiver", "Receiver", 1, ("2 Receiver",), ("2",), 2, 2, "body"),
        ]
        new_sections = [
            Section("S3", "1 Transmitter", "Transmitter", 1, ("1 Transmitter",), ("1",), 1, 1, "body")
        ]

        groups = reporting_module._paired_table_visuals(  # 走完整单侧分组和跨版本配对，复现旧版抵消路径。
            [old_a, old_b],
            [new_combined],
            old_sections=old_sections,
            new_sections=new_sections,
        )

        self.assertEqual(2, len(groups))  # A 与新版合并表配对，旧 B 必须另留删除/移动证据。
        self.assertTrue(any(group.old_tables == (old_a,) and group.new_tables == (new_combined,) for group in groups))
        self.assertTrue(any(group.old_tables == (old_b,) and not group.new_tables for group in groups))

    def test_repeated_boundary_row_cannot_override_proven_continuation_context_conflict(self) -> None:
        """Row and page-edge evidence cannot override incompatible top-level sections."""

        caption = "Table 1. Shared Limits"
        boundary = "表格行: T1 | Parameter=Boundary | Value=stable"
        stable = "表格行: T1 | Parameter=Stable | Value=1"
        old_head = TableVisual(
            1, 1, caption, (60, 600, 560, 720), "", [stable, boundary], "rows",
        )
        new_head = replace(old_head)
        old_independent = TableVisual(
            2, 1, "", (70, 50, 570, 230), "",
            [boundary, "表格行: T1 | Parameter=ReceiverOnly | Value=2"],
            "rows", is_continuation=True,
        )
        new_independent = TableVisual(
            2, 1, "", (70, 50, 570, 230), "",
            [boundary, "表格行: T1 | Parameter=ComplianceOnly | Value=3"],
            "rows", is_continuation=True,
        )
        old_sections = [
            Section("O1", "1 Transmitter", "Transmitter", 1, ("1 Transmitter",), ("1",), 1, 1, "body"),
            Section("O2", "2 Receiver", "Receiver", 1, ("2 Receiver",), ("2",), 2, 2, "body"),
        ]
        new_sections = [
            Section("N1", "1 Transmitter", "Transmitter", 1, ("1 Transmitter",), ("1",), 1, 1, "body"),
            Section("N2", "3 Compliance", "Compliance", 1, ("3 Compliance",), ("3",), 2, 2, "body"),
        ]
        result = DiffResult(
            Path("old.pdf"), Path("new.pdf"), old_sections, new_sections, [], [],
            old_table_visuals=[old_head, old_independent],
            new_table_visuals=[new_head, new_independent],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertEqual(
            ["added", "deleted"],
            sorted(change["change_type"] for change in payload["table_changes"]),
        )
        self.assertTrue(
            all(len(change["old_pages"]) + len(change["new_pages"]) == 1 for change in payload["table_changes"])
        )

    def test_deep_sibling_number_alone_cannot_absorb_an_untitled_table(self) -> None:
        """A shared numeric prefix is not evidence that two independent tables continue."""

        caption = "Table 31-10. Interference Tolerance Test Parameters"
        head_row = "表格行: T1 | Parameter=SharedHead | Value=1"
        old_head = TableVisual(1, 1, caption, (0, 0, 100, 100), "", [head_row], "rows")
        new_head = replace(old_head)
        old_independent = TableVisual(
            2, 1, "", (0, 0, 100, 100), "",
            ["表格行: T1 | Parameter=ReceiverOnly | Value=2"],
            "rows", is_continuation=True,
        )
        new_independent = TableVisual(
            2, 1, "", (0, 0, 100, 100), "",
            ["表格行: T1 | Parameter=DiagnosticsOnly | Value=3"],
            "rows", is_continuation=True,
        )
        old_sections = [
            Section("O1", "31.3.16 Interference", "Interference", 3, ("31.3.16 Interference",), ("31.3.16",), 1, 1, "body"),
            Section("O2", "31.3.17 Receiver", "Receiver", 3, ("31.3.17 Receiver",), ("31.3.17",), 2, 2, "body"),
        ]
        new_sections = [
            Section("N1", "31.3.16 Interference", "Interference", 3, ("31.3.16 Interference",), ("31.3.16",), 1, 1, "body"),
            Section("N2", "31.3.18 Diagnostics", "Diagnostics", 3, ("31.3.18 Diagnostics",), ("31.3.18",), 2, 2, "body"),
        ]
        result = DiffResult(
            Path("old.pdf"), Path("new.pdf"), old_sections, new_sections, [], [],
            old_table_visuals=[old_head, old_independent],
            new_table_visuals=[new_head, new_independent],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertEqual(
            ["added", "deleted"],
            sorted(change["change_type"] for change in payload["table_changes"]),
        )
        self.assertTrue(
            all(len(change["old_pages"]) + len(change["new_pages"]) == 1 for change in payload["table_changes"])
        )

    def test_same_section_alone_cannot_absorb_an_untitled_table(self) -> None:
        """A multi-page section may contain independent untitled tables."""

        caption = "Table 31-10. Interference Tolerance Test Parameters"
        head_row = "表格行: T1 | Parameter=SharedHead | Value=1"
        page_bbox = (0.0, 0.0, 612.0, 1000.0)
        old_head = TableVisual(
            1, 1, caption, (60, 500, 560, 650), "", [head_row], "rows",
            page_bbox=page_bbox,
        )
        new_head = replace(old_head)
        old_independent = TableVisual(
            2, 1, "", (60, 200, 560, 350), "",
            ["表格行: T1 | Parameter=ReceiverAmplitudeThreshold | Value=2"],
            "rows", is_continuation=True, page_bbox=page_bbox,
        )
        new_independent = TableVisual(
            2, 1, "", (60, 200, 560, 350), "",
            ["表格行: T1 | Parameter=DiagnosticControlMode | Value=3"],
            "rows", is_continuation=True, page_bbox=page_bbox,
        )
        old_section = Section(
            "O1", "31.3.16 Interference", "Interference", 3,
            ("31.3.16 Interference",), ("31.3.16",), 1, 2, "body",
        )
        new_section = replace(old_section, section_id="N1")
        result = DiffResult(
            Path("old.pdf"), Path("new.pdf"), [old_section], [new_section], [], [],
            old_table_visuals=[old_head, old_independent],
            new_table_visuals=[new_head, new_independent],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = write_reports(result, temp_dir, DiffOptions())
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

        self.assertEqual(
            ["added", "deleted"],
            sorted(change["change_type"] for change in payload["table_changes"]),
        )
        self.assertTrue(
            all(len(change["old_pages"]) + len(change["new_pages"]) == 1 for change in payload["table_changes"])
        )

    def test_page_edge_geometry_alone_cannot_hide_an_independent_table_move(self) -> None:
        """Aligned page-edge boxes are hints, not proof that two tables are one run."""

        caption = "Table 1. Receiver limits"
        row_a = "表格行: T1 | Parameter=Receiver | Value=1"
        row_b = "表格行: T1 | Parameter=Unrelated diagnostics | Value=2"
        page_bbox = (0.0, 0.0, 612.0, 1000.0)
        old_head = TableVisual(
            1,
            1,
            caption,
            (60.0, 800.0, 560.0, 995.0),
            "",
            [row_a],
            "rows",
            page_bbox=page_bbox,
        )
        old_independent = TableVisual(
            2,
            1,
            "",
            (60.0, 5.0, 560.0, 200.0),
            "",
            [row_b],
            "rows",
            is_continuation=True,
            page_bbox=page_bbox,
        )
        new_combined = TableVisual(
            1,
            1,
            caption,
            (60.0, 800.0, 560.0, 995.0),
            "",
            [row_a, row_b],
            "rows",
            page_bbox=page_bbox,
        )
        old_section = Section(
            "O1",
            "1 Receiver",
            "Receiver",
            1,
            ("1 Receiver",),
            ("1",),
            1,
            2,
            "body",
        )
        new_section = replace(old_section, section_id="N1", end_page=1)

        groups = reporting_module._paired_table_visuals(
            [old_head, old_independent],
            [new_combined],
            old_sections=[old_section],
            new_sections=[new_section],
        )
        result = DiffResult(
            Path("old.pdf"),
            Path("new.pdf"),
            [old_section],
            [new_section],
            [],
            [],
            old_table_visuals=[old_head, old_independent],
            new_table_visuals=[new_combined],
        )

        self.assertEqual(2, len(groups))
        self.assertTrue(
            any(group.old_tables == (old_independent,) and not group.new_tables for group in groups)
        )
        self.assertTrue(reporting_module._build_table_changes(result))

    def test_sequential_identity_cannot_bridge_different_explicit_table_schemas(self) -> None:
        """Equal field counts and P2→P3 do not make Parameter/Value equal Symbol/Units."""

        page_bbox = (0.0, 0.0, 612.0, 1000.0)
        previous = TableVisual(
            1,
            1,
            "Table 1. Receiver limits",
            (60.0, 800.0, 560.0, 995.0),
            "",
            ["表格行: T1 | Parameter=P2 | Value=2"],
            "rows",
            page_bbox=page_bbox,
        )
        current = TableVisual(
            2,
            1,
            "",
            (60.0, 5.0, 560.0, 200.0),
            "",
            ["表格行: T1 | Symbol=P3 | Units=V"],
            "rows",
            is_continuation=True,
            page_bbox=page_bbox,
        )
        section = Section(
            "S1",
            "1 Receiver",
            "Receiver",
            1,
            ("1 Receiver",),
            ("1",),
            1,
            2,
            "body",
        )

        self.assertFalse(
            reporting_module._table_rows_support_sequential_identity_continuation(
                previous,
                current,
            )
        )
        grouped = reporting_module._table_visual_indexes_by_caption_key(
            [previous, current],
            [section],
        )
        self.assertEqual(1, len(grouped))
        self.assertEqual([0], next(iter(grouped.values())))

    def test_page_edge_continuation_accepts_a_repeated_generic_header_schema(self) -> None:
        """A repeated Column-N header plus page geometry can prove a true continuation."""

        page_bbox = (0.0, 0.0, 612.0, 1000.0)
        previous = TableVisual(
            1,
            1,
            "Table 1. Receiver limits",
            (60.0, 800.0, 560.0, 995.0),
            "",
            [
                "表格行: T1 | Parameter=Test pattern | Host Test=QPRBS31 | "
                "Module Test 1\\n(low loss)\\nNote1=QPRBS31 | "
                "Module Test 2\\n(high loss)=QPRBS31 | Units="
            ],
            "rows",
            page_bbox=page_bbox,
        )
        continuation = TableVisual(
            2,
            1,
            "",
            (60.0, 5.0, 560.0, 200.0),
            "",
            [
                "表格行: T1 | Column 1=Parameter | Column 2=Host Test | "
                "Column 3=Module Test 1\\n(low loss)\\nNote1 | "
                "Column 4=Module Test 2\\n(high loss) | Column 5=1\\nUnits2\\n3"
            ],
            "rows",
            is_continuation=True,
            page_bbox=page_bbox,
        )
        section = Section(
            "S1",
            "1 Receiver",
            "Receiver",
            1,
            ("1 Receiver",),
            ("1",),
            1,
            2,
            "body",
        )

        self.assertTrue(
            reporting_module._table_rows_support_repeated_header_continuation(
                previous,
                continuation,
            )
        )
        grouped = reporting_module._table_visual_indexes_by_caption_key(
            [previous, continuation],
            [section],
        )
        self.assertEqual([0, 1], next(iter(grouped.values())))

    def test_page_edge_continuation_accepts_reliable_schema_across_section_boundary(self) -> None:
        """A page-edge split may cross the heading that follows the continued table."""

        page_bbox = (0.0, 0.0, 612.0, 1000.0)
        previous = TableVisual(
            1,
            1,
            "Table 1. Receiver limits",
            (60.0, 800.0, 560.0, 995.0),
            "",
            [
                "表格行: T1 | Parameter=Test pattern | Host Test=QPRBS31 | "
                "Module Test 1=QPRBS31 | Module Test 2=QPRBS31 | Units="
            ],
            "rows",
            page_bbox=page_bbox,
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        continuation = TableVisual(
            2,
            1,
            "",
            (60.0, 5.0, 560.0, 200.0),
            "",
            [
                "表格行: T1 | Parameter=Pre-FEC BER | Host Test=< 5e-6 | "
                "Module Test 1= | Module Test 2= | Units="
            ],
            "rows",
            is_continuation=True,
            page_bbox=page_bbox,
            content_fully_represented=True,
            row_alignment_reliable=True,
        )
        sections = [
            Section(
                "S1",
                "31.3.13 Receiver measurements",
                "Receiver measurements",
                3,
                ("31.3.13 Receiver measurements",),
                ("31.3.13",),
                1,
                1,
                "body",
            ),
            Section(
                "S2",
                "31.3.17.1 Test procedure",
                "Test procedure",
                4,
                ("31.3.17.1 Test procedure",),
                ("31.3.17.1",),
                2,
                2,
                "body",
            ),
        ]

        grouped = reporting_module._table_visual_indexes_by_caption_key(
            [previous, continuation],
            sections,
        )

        self.assertEqual(1, len(grouped))
        self.assertEqual([0, 1], next(iter(grouped.values())))

    def test_renumbered_table_with_same_descriptive_caption_pairs_across_ambiguous_context(self) -> None:
        """A stable descriptive caption may override a page-level context mismatch."""

        old_tables = [
            TableVisual(
                22,
                1,
                "Table 32-11. Receiver Jitter Tolerance Parameters",
                (0, 0, 100, 100),
                "",
                ["表格行: T1 | Parameter=Tolerance | Value=0.05 UI"],
                "rows",
            ),
            TableVisual(
                19,
                1,
                "Table 32-10. Receiver Interference Tolerance Parameters",
                (0, 0, 100, 100),
                "",
                ["表格行: T1 | Parameter=Interference | Value=0.10 UI"],
                "rows",
            ),
        ]
        new_tables = [
            replace(
                old_tables[0],
                title="Table 32-9. Receiver Jitter Tolerance Parameters",
                row_texts=["表格行: T1 | Parameter=Tolerance | Value=0.06 UI"],
            ),
            replace(
                old_tables[1],
                title="Table 32-8. Receiver Interference Tolerance Parameters",
                row_texts=["表格行: T1 | Parameter=Interference | Value=0.11 UI"],
            ),
        ]
        old_section = Section(
            "S1",
            "32.3.2.5 Receiver Jitter Tolerance",
            "Receiver Jitter Tolerance",
            4,
            ("32.3.2.5 Receiver Jitter Tolerance",),
            ("32.3.2.5",),
            22,
            22,
            "body",
        )
        new_section = Section(
            "S2",
            "32.3.2.6 Single Ended Input",
            "Single Ended Input",
            4,
            ("32.3.2.6 Single Ended Input",),
            ("32.3.2.6",),
            22,
            22,
            "body",
        )

        groups = reporting_module._paired_table_visuals(
            old_tables,
            new_tables,
            old_sections=[old_section],
            new_sections=[new_section],
        )

        self.assertEqual(2, len(groups))
        self.assertTrue(all(group.old_tables and group.new_tables for group in groups))

    def test_one_descriptive_caption_does_not_prove_cross_context_identity(self) -> None:
        """One matching long caption remains add/delete when section identity disagrees."""

        old_table = TableVisual(
            1,
            1,
            "Table 32-11. Receiver Jitter Tolerance Parameters",
            (0, 0, 1, 1),
            "",
            ["old"],
            "rows",
        )
        new_table = replace(
            old_table,
            title="Table 32-9. Receiver Jitter Tolerance Parameters",
            row_texts=["new"],
        )
        old_section = Section("S1", "1 First", "First", 1, ("1 First",), ("1",), 1, 1, "body")
        new_section = Section("S2", "2 Second", "Second", 1, ("2 Second",), ("2",), 1, 1, "body")

        groups = reporting_module._paired_table_visuals(
            [old_table],
            [new_table],
            old_sections=[old_section],
            new_sections=[new_section],
        )

        self.assertEqual(2, len(groups))
        self.assertTrue(all(bool(group.old_tables) != bool(group.new_tables) for group in groups))

    def test_unique_exact_numbered_caption_survives_section_locator_drift(self) -> None:
        """One unique exact table identity may move when section extraction drifts."""

        old_table = TableVisual(
            9,
            1,
            "Table 32-4. Transmitter Output Jitter Specification",
            (0, 0, 1, 1),
            "",
            ["表格行: T1 | Parameter=Jitter | Value=0.05 UI"],
            "rows",
        )
        new_table = replace(
            old_table,
            page_number=11,
            row_texts=["表格行: T1 | Parameter=Jitter | Value=0.06 UI"],
        )
        old_section = Section(
            "S1",
            "32.2.4.5 Old",
            "Old",
            4,
            ("32.2.4.5 Old",),
            ("32.2.4.5",),
            9,
            9,
            "body",
        )
        new_section = Section(
            "S2",
            "32.3.1 New",
            "New",
            3,
            ("32.3.1 New",),
            ("32.3.1",),
            11,
            11,
            "body",
        )

        groups = reporting_module._paired_table_visuals(
            [old_table],
            [new_table],
            old_sections=[old_section],
            new_sections=[new_section],
        )

        self.assertEqual(1, len(groups))
        self.assertTrue(groups[0].old_tables and groups[0].new_tables)

    def test_unique_exact_caption_pairs_one_page_with_split_continuation(self) -> None:
        """A uniquely captioned table must pair when only the new revision spans two pages."""

        title = "Table 31-10. Interference Tolerance Test Parameters"  # 真实 OIF 表在新版由一页拆为表题页加续页。
        old_table = TableVisual(  # 旧版七行都在同一视觉表中。
            17,
            1,
            title,
            (62.0, 121.0, 562.0, 349.0),
            "",
            [f"表格行: T1 | Parameter=P{index} | Value={index}" for index in range(7)],
            "rows",
            page_bbox=(0.0, 0.0, 612.0, 792.0),
        )
        new_first = TableVisual(  # 新版第一页保留唯一表题，但只含前三行。
            15,
            1,
            title,
            (62.0, 594.0, 561.0, 714.0),
            "",
            [f"表格行: T1 | Parameter=P{index} | Value={index}" for index in range(3)],
            "rows",
            page_bbox=(0.0, 0.0, 612.0, 792.0),
        )
        new_continuation = TableVisual(  # 下一页无表题，与上页表格具有对齐的跨页边界几何。
            16,
            1,
            "",
            (70.0, 53.0, 570.0, 233.0),
            "",
            [
                f"表格行: T1 | Column 1=P{index} | Column 2={'revised' if index == 6 else index}"
                for index in range(3, 7)
            ],  # 真实 PDF 的续页可能失去表头，只剩通用 Column N；第一列身份仍须参与配对。
            "rows",
            is_continuation=True,
            page_bbox=(0.0, 0.0, 612.0, 792.0),
        )
        old_section = Section(  # 章节在两个修订版中重编号，不能阻断唯一表号身份。
            "S1", "31.3.15 Interference Tolerance", "Interference Tolerance", 3,
            ("31.3.15 Interference Tolerance",), ("31.3.15",), 17, 17, "body",
        )
        new_sections = [
            Section(
                "S2", "31.3.16 Interference Tolerance", "Interference Tolerance", 3,
                ("31.3.16 Interference Tolerance",), ("31.3.16",), 15, 15, "body",
            ),
            Section(
                "S3", "31.3.17 Jitter Tolerance", "Jitter Tolerance", 3,
                ("31.3.17 Jitter Tolerance",), ("31.3.17",), 16, 16, "body",
            ),
        ]

        groups = reporting_module._paired_table_visuals(  # 走报告实际表格分组与跨版本配对入口。
            [old_table],
            [new_first, new_continuation],
            old_sections=[old_section],
            new_sections=new_sections,
        )

        self.assertEqual(1, len(groups))  # 同一张唯一编号表不得误报为旧表删除加两个新表新增。
        self.assertEqual((old_table,), groups[0].old_tables)  # 旧侧保留单页逻辑组。
        self.assertEqual((new_first, new_continuation), groups[0].new_tables)  # 新侧续页归入同组。

        result = DiffResult(  # 直接构造报告事实，隔离 HTML 文件写入并检查 JSON 序列化口径。
            Path("old.pdf"),
            Path("new.pdf"),
            [old_section],
            new_sections,
            [],
            [],
            old_table_visuals=[old_table],
            new_table_visuals=[new_first, new_continuation],
        )
        table_change = reporting_module._build_table_changes(result)[0]  # P6 值变化确保该配对进入报告。
        payload = reporting_module._table_change_to_dict(table_change)  # JSON 的 pair_similarity 是用户审计面。
        group_score = reporting_module._table_visual_group_similarity(
            (old_table,),
            (new_first, new_continuation),
        )
        first_page_score = reporting_module._table_visual_similarity(old_table, new_first)

        self.assertNotAlmostEqual(first_page_score, group_score)  # 该反例必须区分旧首屏分数和真实整组分数。
        self.assertEqual(round(group_score, 6), payload["pair_similarity"])  # 展示值必须与实际整组门槛一致。

    def test_unique_exact_caption_does_not_pair_disjoint_cross_schema_rows(self) -> None:
        """An exact table title cannot override unrelated first-column identities."""

        title = "Table 9-7. Receiver Limits"
        old_table = TableVisual(
            4,
            1,
            title,
            (60.0, 100.0, 560.0, 300.0),
            "",
            [
                "表格行: T1 | Parameter=Output jitter | Value=0.02 UI",
                "表格行: T1 | Parameter=Return loss | Value=12 dB",
            ],
            "rows",
        )
        new_table = TableVisual(
            8,
            1,
            title,
            (60.0, 100.0, 560.0, 300.0),
            "",
            [
                "表格行: T1 | Column 1=Supply voltage | Column 2=3.3 V",
                "表格行: T1 | Column 1=Input current | Column 2=40 mA",
            ],
            "rows",
        )
        old_section = Section(
            "S1", "9.2 Receiver", "Receiver", 2,
            ("9.2 Receiver",), ("9.2",), 4, 4, "body",
        )
        new_section = Section(
            "S2", "9.5 Power", "Power", 2,
            ("9.5 Power",), ("9.5",), 8, 8, "body",
        )

        groups = reporting_module._paired_table_visuals(
            [old_table],
            [new_table],
            old_sections=[old_section],
            new_sections=[new_section],
        )

        self.assertEqual(2, len(groups))
        self.assertEqual(1, sum(bool(group.old_tables) for group in groups))
        self.assertEqual(1, sum(bool(group.new_tables) for group in groups))
        self.assertTrue(all(not (group.old_tables and group.new_tables) for group in groups))

    def test_symbol_identity_pairs_with_generic_first_column_across_schema_drift(self) -> None:
        """A Symbol-first table keeps row identity when a continuation loses its header."""

        title = "Table 9-8. Jitter Limits"
        old_table = TableVisual(
            4, 1, title, (60.0, 100.0, 560.0, 300.0), "",
            [
                "表格行: T1 | Symbol=JH4u | Max=0.118 | Unit=UI",
                "表格行: T1 | Symbol=EOJ03 | Max=0.025 | Unit=UI",
                "表格行: T1 | Symbol=JRMS | Max=0.023 | Unit=UI",
            ],
            "rows",
        )
        new_table = TableVisual(
            8, 1, title, (60.0, 100.0, 560.0, 300.0), "",
            [
                "表格行: T1 | Column 1=JH4u | Column 2=0.118 | Column 3=UI",
                "表格行: T1 | Column 1=EOJ03 | Column 2=0.025 | Column 3=UI",
                "表格行: T1 | Column 1=JRMS | Column 2=0.023 | Column 3=UI",
            ],
            "rows",
        )
        old_section = Section(
            "S1", "9.2 Jitter", "Jitter", 2,
            ("9.2 Jitter",), ("9.2",), 4, 4, "body",
        )
        new_section = Section(
            "S2", "9.5 Jitter", "Jitter", 2,
            ("9.5 Jitter",), ("9.5",), 8, 8, "body",
        )

        groups = reporting_module._paired_table_visuals(
            [old_table],
            [new_table],
            old_sections=[old_section],
            new_sections=[new_section],
        )

        self.assertEqual(1, len(groups))
        self.assertEqual((old_table,), groups[0].old_tables)
        self.assertEqual((new_table,), groups[0].new_tables)

    def test_two_generic_continuations_pair_rows_by_unique_first_column(self) -> None:
        """An inserted row cannot shift every following Column N row comparison."""

        title = "Table 9-9. Receiver Parameters"
        old_table = TableVisual(
            4, 1, title, (60.0, 100.0, 560.0, 300.0), "",
            [
                "表格行: T1 | Column 1=P1 | Column 2=10",
                "表格行: T1 | Column 1=P2 | Column 2=20",
            ],
            "rows",
        )
        new_table = TableVisual(
            5, 1, title, (60.0, 100.0, 560.0, 320.0), "",
            [
                "表格行: T1 | Column 1=P1 | Column 2=10",
                "表格行: T1 | Column 1=X | Column 2=15",
                "表格行: T1 | Column 1=P2 | Column 2=20",
            ],
            "rows",
        )

        changes = reporting_module._table_row_changes((old_table,), (new_table,))

        self.assertEqual(1, len(changes))
        self.assertEqual("X", changes[0].item)
        self.assertEqual("新表新增行", changes[0].change_type)

    def test_generic_column_boundary_drift_requires_table_wide_evidence(self) -> None:
        """One flattened row alone cannot authorize a cross-column equality claim."""

        old_row = (
            "表格行: T1 | Column 1=Short | Column 2=near-end | "
            "Column 3=0 | Column 4=0"
        )
        new_row = (
            "表格行: T1 | Column 1=Short | Column 2=near-end 0 | Column 3=0"
        )

        self.assertNotEqual(
            "无变化",
            reporting_module._table_structured_diff_kind(old_row, new_row),
        )

        changed_row = new_row.replace("Column 3=0", "Column 3=8")
        self.assertNotEqual(
            "无变化",
            reporting_module._table_structured_diff_kind(old_row, changed_row),
        )

    def test_isolated_untitled_one_cell_image_fragment_is_not_a_table_change(self) -> None:
        """A one-cell label cut from a figure cannot be promoted to an added table."""

        fragment = TableVisual(
            18,
            1,
            "",
            (327.7, 164.5, 390.3, 279.0),
            "data:image/jpeg;base64,AAAA",
            ["表格行: T1 | Column 1=MCB"],
            "OpenCV 网格检测: 横线 8 条，竖线 4 条",
            ocr_text="n e = ee",
            ocr_status="OCR 已执行。",
            is_continuation=True,
        )
        section = Section(
            "S1", "1 Figure context", "Figure context", 1,
            ("1 Figure context",), ("1",), 18, 18, "MCB figure label",
        )
        result = DiffResult(
            Path("old.pdf"),
            Path("new.pdf"),
            [section],
            [section],
            [],
            [],
            old_table_visuals=[],
            new_table_visuals=[fragment],
        )

        self.assertEqual([], reporting_module._build_table_changes(result))

        titled_table = replace(fragment, title="Table 1-1. MCB State")
        titled_result = replace(result, new_table_visuals=[titled_table])
        titled_changes = reporting_module._build_table_changes(titled_result)
        self.assertEqual(1, len(titled_changes))
        self.assertEqual("added", titled_changes[0].change_type)

    def test_real_oif_named_tables_keep_untitled_pages_with_deep_section_continuity(self) -> None:
        """Table 31-10/31-12 用真实边界行证据保住无题续页。"""

        cases = (
            ("Table 31-10. Interference Tolerance Test Parameters", "31.3.16", "31.3.17"),
            ("Table 31-12. Emulated Host Channels", "31.3.18", "31.3.18.1"),
        )
        for title, first_number, next_number in cases:
            with self.subTest(title=title):
                boundary = "表格行: T1 | Parameter=Boundary | Value=stable"
                tables = [  # 上页末行与下页首行重复，模拟 PDF 跨页续表的可核对内容边界。
                    TableVisual(1, 1, title, (0, 0, 100, 100), "", ["表格行: T1 | Parameter=A | Value=1", boundary], "rows"),
                    TableVisual(2, 1, "", (0, 0, 100, 100), "", [boundary, "表格行: T1 | Parameter=B | Value=2"], "rows", is_continuation=True),
                ]
                sections = [
                    Section("S1", f"{first_number} First", "First", first_number.count(".") + 1, (f"{first_number} First",), (first_number,), 1, 1, "body"),
                    Section("S2", f"{next_number} Next", "Next", next_number.count(".") + 1, (f"{next_number} Next",), (next_number,), 2, 2, "body"),
                ]

                grouped = reporting_module._table_visual_indexes_by_caption_key(tables, sections)

                self.assertEqual([[0, 1]], list(grouped.values()))  # 深层 sibling/child 结构与边界行同时成立才合并。

    def test_neutral_to_explicit_header_only_suppresses_proven_equal_cells(self) -> None:
        """Header recognition drift cannot erase an ambiguous symbol operator."""

        old_rd = (
            "表格行: T1 | Column 1=Single-ended termination resistance | "
            "Column 2=R / d | Column 3=46.25 | Column 4=Ω"
        )
        new_rd = "表格行: T1 | Parameter=Single-ended termination resistance | Symbol=Rd | Value=46.25 | Units=Ω"
        old_bw = "表格行: T1 | Column 1=Receiver 3 dB bandwidth | Column 2=f | Column 3=0.55 × f | Column 4=GHz"
        new_bw = "表格行: T1 | Parameter=Receiver 3 dB bandwidth | Symbol=f | Value=0.55 × f | Units=GHz"

        self.assertNotEqual(
            "无变化",
            reporting_module._table_structured_diff_kind(old_rd, new_rd),
        )  # R / d 也可能是比值；没有字形坐标时不能猜成下标 Rd。
        self.assertEqual("无变化", reporting_module._table_structured_diff_kind(old_bw, new_bw))
        old_table = TableVisual(
            1,
            1,
            "Table 32-1. COM Values",
            (0, 0, 1, 1),
            "",
            [old_bw],
            "rows",
        )
        new_table = replace(old_table, row_texts=[new_bw])
        self.assertEqual(
            [],
            reporting_module._table_row_changes((old_table,), (new_table,)),
        )
        self.assertNotEqual(
            "无变化",
            reporting_module._table_structured_diff_kind(
                old_rd, new_rd.replace("46.25", "45.00")
            ),
        )

    def test_numeric_multiplication_x_and_times_glyph_are_equal(self) -> None:
        """A multiplication glyph change must not outrank real numeric changes."""

        old_row = "表格行: T1 | Parameter=Loss coefficient | Value=5.0x10-4 | Units=1/mm"
        new_row = old_row.replace("x10", "×10")

        self.assertEqual(
            "无变化",
            reporting_module._table_structured_diff_kind(old_row, new_row),
        )


if __name__ == "__main__":
    unittest.main()
