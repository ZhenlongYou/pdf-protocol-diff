"""Reader regression tests for screenshot-backed long prose changes."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from protocol_pdf_diff.compare import compare_extractions, run_diff
from protocol_pdf_diff.models import (
    DiffOptions,
    DocumentBlock,
    DocumentBlockKind,
    TableVisual,
)
from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.prose_source_visuals import (
    _annotated_crop,
    _highlight_boxes,
    build_prose_source_visuals,
)
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.sample_data import write_multipage_text_pdf
from protocol_pdf_diff.visual_watchdog import _snapshot_pdf as snapshot_pdf


class _FirstViewText(HTMLParser):
    """Collect text visible before closed report details are expanded."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_details = 0
        self.in_summary = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "details" and not any(name == "open" for name, _value in attrs):
            self.hidden_details += 1
        elif tag == "summary":
            self.in_summary += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "summary" and self.in_summary:
            self.in_summary -= 1
        elif tag == "details" and self.hidden_details:
            self.hidden_details -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_details or self.in_summary:
            self.parts.append(data)


class ProseSourceVisualReportTests(unittest.TestCase):
    """Exercise the real PDF -> comparison -> standalone HTML path."""

    def test_long_modified_prose_uses_aligned_source_crops_and_collapsed_text(
        self,
    ) -> None:
        """Long prose must lead with old/new source crops, not a wall of diff text."""

        common = [
            "The receiver calibration procedure records the signal generator state.",
            "The operator shall preserve the measurement bandwidth and pattern identity.",
        ]
        old_steps = [
            f"Calibration step {index} uses an old limit of {100 + index} mV and records the result."
            for index in range(1, 13)
        ]
        new_steps = [
            f"Calibration step {index} uses a new limit of {120 + index} mV and records the result."
            for index in range(1, 13)
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                root / "old calibration.pdf",
                [["1 Receiver Calibration", *common, *old_steps]],
            )
            new_pdf = write_multipage_text_pdf(
                root / "new calibration.pdf",
                [["1 Receiver Calibration", *common, *new_steps]],
            )

            result = run_diff(
                old_pdf,
                new_pdf,
                DiffOptions(visual_watchdog=False, max_snippets_per_section=20),
            )
            outputs = write_reports(result, root / "reports", DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

            self.assertIn('class="prose-source-visual-grid"', html)
            self.assertIn("旧版原文区域", html)
            self.assertIn("新版原文区域", html)
            self.assertGreaterEqual(html.count("data:image/jpeg;base64,"), 2)
            self.assertIn('class="prose-text-details"', html)
            self.assertIn("查看文字识别明细", html)
            self.assertIn("原文坐标浅色标注", html)

            first_view = _FirstViewText()
            first_view.feed(html)
            first_view.close()
            self.assertNotIn(old_steps[0], "".join(first_view.parts))
            self.assertIn("Calibration step 1 uses", html)
            self.assertIn("101", html)

            visuals = payload["prose_source_visuals"]
            self.assertEqual(1, len(visuals))
            self.assertEqual([1], visuals[0]["old_pages"])
            self.assertEqual([1], visuals[0]["new_pages"])
            self.assertGreater(visuals[0]["old_highlight_region_count"], 0)
            self.assertGreater(visuals[0]["new_highlight_region_count"], 0)
            self.assertNotIn("image_data_uri", json.dumps(visuals))

    def test_short_change_keeps_compact_text_without_source_screenshot(self) -> None:
        """A small wording edit should not pay the visual weight of a PDF crop."""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                root / "old.pdf",
                [["1 Receiver", "The receiver limit is 100 mV."]],
            )
            new_pdf = write_multipage_text_pdf(
                root / "new.pdf",
                [["1 Receiver", "The receiver limit is 120 mV."]],
            )

            result = run_diff(old_pdf, new_pdf, DiffOptions(visual_watchdog=False))
            outputs = write_reports(result, root / "reports", DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")
            payload = json.loads(outputs["json"].read_text(encoding="utf-8"))

            self.assertNotIn('class="prose-source-visual-grid"', html)
            self.assertNotIn('class="prose-text-details"', html)
            self.assertIn("100", html)
            self.assertIn("120", html)
            self.assertEqual([], payload["prose_source_visuals"])

    def test_source_highlight_is_translucent_enough_to_keep_text_visible(self) -> None:
        """A pale overlay may tint the source but must not turn text into a color block."""

        source = Image.new("RGB", (120, 80), "white")
        draw = ImageDraw.Draw(source)
        draw.rectangle((35, 35, 85, 45), fill="black")

        _crop_bbox, annotated, region_count = _annotated_crop(
            source,
            page_bbox=(0.0, 0.0, 120.0, 80.0),
            highlight_boxes=((20.0, 20.0, 100.0, 60.0),),
        )

        self.assertEqual(1, region_count)
        text_pixel = annotated.getpixel((60, 40))
        background_pixel = annotated.getpixel((60, 30))
        self.assertLess(max(text_pixel), 50)
        self.assertNotEqual(source.getpixel((60, 30)), background_pixel)
        self.assertGreater(min(background_pixel), 220)

    def test_source_highlights_exclude_and_clip_proven_gutter_numbers(self) -> None:
        """Printed line numbers must remain outside even when glued to body text."""

        blocks = (
            DocumentBlock(
                page_number=1,
                bbox=(3.0, 10.0, 9.0, 18.0),
                kind=DocumentBlockKind.TEXT,
                text="25",
                reading_order=0,
                source_engine="test",
            ),
            DocumentBlock(
                page_number=1,
                bbox=(3.0, 10.0, 100.0, 20.0),
                kind=DocumentBlockKind.TEXT,
                text="25 Receiver limit is 25 mV.",
                reading_order=1,
                source_engine="test",
            ),
            DocumentBlock(
                page_number=1,
                bbox=(20.0, 21.0, 100.0, 31.0),
                kind=DocumentBlockKind.TEXT,
                text="The operator records the result.",
                reading_order=2,
                source_engine="test",
            ),
        )

        regions, matched_snippets = _highlight_boxes(
            blocks,
            ("Receiver limit is 25 mV. The operator records the result.",),
            excluded_bboxes=((0.0, 0.0, 12.0, 80.0),),
        )

        self.assertEqual(1, matched_snippets)
        self.assertEqual(
            ((12.0, 10.0, 100.0, 20.0), (20.0, 21.0, 100.0, 31.0)),
            regions,
        )

    def test_recognized_table_region_is_not_repeated_as_colored_prose_evidence(
        self,
    ) -> None:
        """A table card owns its source area; prose screenshots must not tint it again."""

        old_rows = [
            f"Receiver parameter item {index} has the old tabulated value {100 + index} mV."
            for index in range(1, 14)
        ]
        new_rows = [
            f"Receiver parameter item {index} has the new tabulated value {120 + index} mV."
            for index in range(1, 14)
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                root / "old table.pdf",
                [["1 Receiver Parameters", *old_rows]],
            )
            new_pdf = write_multipage_text_pdf(
                root / "new table.pdf",
                [["1 Receiver Parameters", *new_rows]],
            )
            old_extraction = extract_pdf_text(old_pdf)
            new_extraction = extract_pdf_text(new_pdf)
            result = compare_extractions(
                old_extraction,
                new_extraction,
                DiffOptions(visual_watchdog=False, max_snippets_per_section=20),
            )

            def table_covering_changed_rows(extraction: object) -> TableVisual:
                page = extraction.pages[0]
                row_blocks = [
                    block
                    for block in page.blocks
                    if "Receiver parameter item" in block.text
                ]
                bbox = (
                    min(block.bbox[0] for block in row_blocks),
                    min(block.bbox[1] for block in row_blocks),
                    max(block.bbox[2] for block in row_blocks),
                    max(block.bbox[3] for block in row_blocks),
                )
                return TableVisual(
                    page_number=1,
                    table_number=1,
                    title="Table 1. Receiver parameters",
                    bbox=bbox,
                    image_data_uri="",
                    row_texts=[block.text for block in row_blocks],
                    grid_summary="test table region",
                    content_fully_represented=True,
                )

            old_table = table_covering_changed_rows(old_extraction)
            new_table = table_covering_changed_rows(new_extraction)
            result = result.__class__(
                **{
                    **result.__dict__,
                    "old_table_visuals": [old_table],
                    "new_table_visuals": [new_table],
                }
            )
            visuals, warnings = build_prose_source_visuals(
                result,
                old_extraction,
                new_extraction,
            )

            self.assertEqual([], warnings)
            self.assertEqual([], visuals)

    def test_snapshot_hash_mismatch_falls_back_to_text_without_stale_images(
        self,
    ) -> None:
        """A changed source snapshot must never be presented as current visual evidence."""

        old_lines = [
            f"Calibration item {index} keeps the old amplitude at {100 + index} mV."
            for index in range(1, 10)
        ]
        new_lines = [
            f"Calibration item {index} keeps the new amplitude at {120 + index} mV."
            for index in range(1, 10)
        ]

        def mismatched_snapshot(path: Path) -> tuple[object, str]:
            stream, _digest = snapshot_pdf(path)
            return stream, "0" * 64

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                root / "old.pdf", [["1 Calibration", *old_lines]]
            )
            new_pdf = write_multipage_text_pdf(
                root / "new.pdf", [["1 Calibration", *new_lines]]
            )
            with mock.patch(
                "protocol_pdf_diff.prose_source_visuals._snapshot_pdf",
                side_effect=mismatched_snapshot,
            ):
                result = run_diff(
                    old_pdf,
                    new_pdf,
                    DiffOptions(visual_watchdog=False),
                )
            outputs = write_reports(result, root / "reports", DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")

            self.assertEqual([], result.prose_source_visuals)
            self.assertTrue(
                any(
                    "渲染快照与文字抽取快照不一致" in warning
                    for warning in result.warnings
                )
            )
            self.assertNotIn('class="prose-source-visual-grid"', html)
            self.assertIn("Calibration item 1 keeps", html)


if __name__ == "__main__":
    unittest.main()
