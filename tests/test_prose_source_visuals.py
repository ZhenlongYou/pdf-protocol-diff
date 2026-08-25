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
    _content_horizontal_bounds,
    _crop_regions,
    _expand_boxes_to_complete_paragraph_lines,
    _figure_crop_bbox,
    _highlight_boxes,
    _page_contains_figure_caption,
    _source_crop,
    _subtract_excluded_regions,
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

    def test_long_modified_prose_leads_with_structured_diff_and_collapses_raw_source(
        self,
    ) -> None:
        """Word-level text is primary; raw PDF crops are optional provenance only."""

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
            self.assertIn('class="prose-source-details"', html)
            self.assertIn("查看原文出处（无颜色对比）", html)
            self.assertNotIn('class="prose-text-details"', html)
            self.assertNotIn("查看文字识别明细", html)
            self.assertNotIn("原文坐标浅色标注", html)
            self.assertLess(html.index('class="compare-grid"'), html.index('class="prose-source-details"'))

            first_view = _FirstViewText()
            first_view.feed(html)
            first_view.close()
            self.assertIn(old_steps[0], "".join(first_view.parts))
            self.assertIn("Calibration step 1 uses", html)
            self.assertIn("101", html)

            visuals = payload["prose_source_visuals"]
            self.assertEqual(1, len(visuals))
            self.assertEqual([1], visuals[0]["old_pages"])
            self.assertEqual([1], visuals[0]["new_pages"])
            self.assertEqual(0, visuals[0]["old_highlight_region_count"])
            self.assertEqual(0, visuals[0]["new_highlight_region_count"])
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
            self.assertNotIn('class="prose-source-details"', html)
            self.assertIn("100", html)
            self.assertIn("120", html)
            self.assertEqual([], payload["prose_source_visuals"])

    def test_figure_only_change_renders_raw_images_without_text_comparison(self) -> None:
        """A Figure change remains visible as old/new raw images, never as label-wall diff."""

        old_figure_text = "Vout = 3.3 V Legacy VMA diagram TP1 TP2"
        new_figure_text = "Vout = 2.5 V Revised VMA diagram TP1a TP3"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_pdf = write_multipage_text_pdf(
                root / "old figure.pdf",
                [["1 Receiver setup", "Figure 1.", old_figure_text]],
            )
            new_pdf = write_multipage_text_pdf(
                root / "new figure.pdf",
                [["1 Receiver setup", "Figure 2.", new_figure_text]],
            )

            result = run_diff(
                old_pdf,
                new_pdf,
                DiffOptions(visual_watchdog=False),
            )
            outputs = write_reports(result, root / "reports", DiffOptions())
            html = outputs["html"].read_text(encoding="utf-8")

            self.assertIn('class="figure-source-visual-grid"', html)
            self.assertIn("Figure 原图（不做文字或颜色自动对比）", html)
            self.assertNotIn("浅色标注", html)
            first_view = _FirstViewText()
            first_view.feed(html)
            first_view.close()
            visible_text = "".join(first_view.parts)
            self.assertNotIn(old_figure_text, visible_text)
            self.assertNotIn(new_figure_text, visible_text)
            self.assertGreaterEqual(html.count("data:image/jpeg;base64,"), 2)

    def test_figure_reference_sentence_is_not_misclassified_as_a_caption(self) -> None:
        """A prose line beginning with Figure N remains text, not a fake image crop."""

        caption = DocumentBlock(
            1,
            (50.0, 80.0, 560.0, 95.0),
            DocumentBlockKind.TEXT,
            "Figure 29-9. Module output test setup",
            0,
            "test",
        )
        reference = DocumentBlock(
            1,
            (50.0, 100.0, 560.0, 115.0),
            DocumentBlockKind.TEXT,
            "Figure 29-9 and the method described in Section 30.4.1 are used.",
            0,
            "test",
        )

        self.assertTrue(_page_contains_figure_caption((caption,)))
        self.assertFalse(_page_contains_figure_caption((reference,)))

    def test_figure_crop_stops_at_geometric_next_heading_even_if_reading_order_is_wrong(self) -> None:
        """A following clause heading must never appear inside a Figure raw image."""

        heading = DocumentBlock(
            1, (40.0, 500.0, 560.0, 520.0), DocumentBlockKind.TEXT,
            "28 29.3.7 AC Common Mode Noise", 0, "test",
        )
        caption = DocumentBlock(
            1, (40.0, 100.0, 560.0, 120.0), DocumentBlockKind.TEXT,
            "Figure 29-3. Measurement of VMA voltage levels", 1, "test",
        )
        diagram_value = DocumentBlock(
            1, (340.0, 350.0, 560.0, 365.0), DocumentBlockKind.TEXT,
            "4.95 dB AC 22", 2, "test",
        )

        crop = _figure_crop_bbox(
            page_bbox=(0.0, 0.0, 612.0, 792.0),
            blocks=(heading, caption, diagram_value),
            caption_index=1,
            blocking_bboxes=(),
            noise_bboxes=(),
        )

        self.assertIsNotNone(crop)
        self.assertGreater(crop[3], diagram_value.bbox[3])
        self.assertLessEqual(crop[3], 492.0)

    def test_figure_crop_stops_before_a_wrapped_following_paragraph(self) -> None:
        """Two ordinary body lines establish the boundary even without punctuation."""

        caption = DocumentBlock(
            1,
            (50.0, 80.0, 560.0, 95.0),
            DocumentBlockKind.TEXT,
            "Figure 30-15. Cabled module channel reference model",
            0,
            "test",
        )
        diagram_label = DocumentBlock(
            1,
            (210.0, 250.0, 400.0, 264.0),
            DocumentBlockKind.TEXT,
            "22.0 dB die-to-die channel loss",
            1,
            "test",
        )
        first_prose_line = DocumentBlock(
            1,
            (72.0, 382.0, 505.0, 394.0),
            DocumentBlockKind.TEXT,
            "The third recommended channel includes a Co-Packaged Optics host that",
            2,
            "test",
        )
        second_prose_line = DocumentBlock(
            1,
            (46.0, 395.0, 230.0, 407.0),
            DocumentBlockKind.TEXT,
            "25 consists of an optical signal at TP3.",
            3,
            "test",
        )

        crop = _figure_crop_bbox(
            page_bbox=(0.0, 0.0, 612.0, 792.0),
            blocks=(caption, diagram_label, first_prose_line, second_prose_line),
            caption_index=0,
            blocking_bboxes=(),
            noise_bboxes=(),
        )

        self.assertIsNotNone(crop)
        self.assertLessEqual(crop[3], first_prose_line.bbox[1] - 8.0)

    def test_figure_crop_stops_before_coordinate_proven_footer_fragments(self) -> None:
        """Word-sized footer boxes form one boundary; a side number column does not."""

        caption = DocumentBlock(
            1,
            (50.0, 80.0, 560.0, 95.0),
            DocumentBlockKind.TEXT,
            "Figure 30-4. S-parameter limit",
            0,
            "test",
        )
        diagram_label = DocumentBlock(
            1,
            (210.0, 500.0, 400.0, 514.0),
            DocumentBlockKind.TEXT,
            "Frequency GHz",
            1,
            "test",
        )
        noise = (
            (555.0, 100.0, 568.0, 700.0),
            (70.0, 718.0, 200.0, 730.0),
            (205.0, 718.0, 350.0, 730.0),
            (355.0, 718.0, 500.0, 730.0),
        )

        crop = _figure_crop_bbox(
            page_bbox=(0.0, 0.0, 612.0, 792.0),
            blocks=(caption, diagram_label),
            caption_index=0,
            blocking_bboxes=(),
            noise_bboxes=noise,
        )

        self.assertIsNotNone(crop)
        self.assertGreater(crop[3], diagram_label.bbox[3])
        self.assertLessEqual(crop[3], 710.0)

    def test_figure_crop_stops_before_a_generic_wide_bottom_furniture_line(self) -> None:
        """A bottom-margin publication line is excluded without matching its wording."""

        caption = DocumentBlock(
            1,
            (50.0, 320.0, 560.0, 335.0),
            DocumentBlockKind.TEXT,
            "Figure 29-1. End-to-end channel",
            0,
            "test",
        )
        diagram_label = DocumentBlock(
            1,
            (210.0, 620.0, 400.0, 634.0),
            DocumentBlockKind.TEXT,
            "Optical fiber",
            1,
            "test",
        )
        bottom_furniture = DocumentBlock(
            1,
            (72.0, 746.0, 532.0, 756.0),
            DocumentBlockKind.TEXT,
            "Example Standards Consortium - Clause 30 4",
            2,
            "test",
        )

        crop = _figure_crop_bbox(
            page_bbox=(0.0, 0.0, 612.0, 792.0),
            blocks=(caption, diagram_label, bottom_furniture),
            caption_index=0,
            blocking_bboxes=(),
            noise_bboxes=(),
        )

        self.assertIsNotNone(crop)
        self.assertGreater(crop[3], diagram_label.bbox[3])
        self.assertLessEqual(crop[3], bottom_furniture.bbox[1] - 8.0)

    def test_figure_crop_stops_before_a_top_level_heading(self) -> None:
        """A simple heading such as ``2 Requirements`` owns the following content."""

        caption = DocumentBlock(
            1, (50.0, 100.0, 560.0, 118.0), DocumentBlockKind.TEXT,
            "Figure 1. Receiver setup", 0, "test",
        )
        diagram_label = DocumentBlock(
            1, (210.0, 500.0, 400.0, 514.0), DocumentBlockKind.TEXT,
            "Host Rx", 1, "test",
        )
        heading = DocumentBlock(
            1, (80.0, 680.0, 540.0, 700.0), DocumentBlockKind.TEXT,
            "2 Requirements", 2, "test",
        )

        crop = _figure_crop_bbox(
            page_bbox=(0.0, 0.0, 612.0, 792.0),
            blocks=(caption, diagram_label, heading),
            caption_index=0,
            blocking_bboxes=(),
            noise_bboxes=(),
        )

        self.assertIsNotNone(crop)
        self.assertGreater(crop[3], diagram_label.bbox[3])
        self.assertLessEqual(crop[3], heading.bbox[1] - 8.0)

    def test_wide_bottom_axis_label_is_not_mistaken_for_page_furniture(self) -> None:
        """A Figure may legitimately use a wide shallow label near the page bottom."""

        caption = DocumentBlock(
            1, (50.0, 100.0, 560.0, 118.0), DocumentBlockKind.TEXT,
            "Figure 1. Receiver response", 0, "test",
        )
        axis_label = DocumentBlock(
            1, (80.0, 730.0, 540.0, 742.0), DocumentBlockKind.TEXT,
            "Frequency (GHz) / Output voltage (mV)", 1, "test",
        )

        crop = _figure_crop_bbox(
            page_bbox=(0.0, 0.0, 612.0, 792.0),
            blocks=(caption, axis_label),
            caption_index=0,
            blocking_bboxes=(),
            noise_bboxes=(),
        )

        self.assertIsNotNone(crop)
        self.assertGreater(crop[3], axis_label.bbox[3])

    def test_figure_crop_stops_before_a_displayed_formula(self) -> None:
        """A Figure card must not absorb a following equation owned by Formula evidence."""

        caption = DocumentBlock(
            1,
            (80.0, 150.0, 540.0, 175.0),
            DocumentBlockKind.TEXT,
            "Figure 29-4. S-parameter limit",
            0,
            "test",
        )
        diagram_label = DocumentBlock(
            1,
            (210.0, 420.0, 400.0, 434.0),
            DocumentBlockKind.TEXT,
            "Frequency GHz",
            1,
            "test",
        )
        formula = DocumentBlock(
            1,
            (150.0, 490.0, 440.0, 504.0),
            DocumentBlockKind.TEXT,
            "SCD11 ≤ -22+16*(f/fb) dB 0.05 GHz ≤ f ≤ fb/2",
            2,
            "test",
        )
        formula_number = DocumentBlock(
            1,
            (508.0, 499.0, 540.0, 512.0),
            DocumentBlockKind.TEXT,
            "(29-1)",
            3,
            "test",
        )

        crop = _figure_crop_bbox(
            page_bbox=(0.0, 0.0, 612.0, 792.0),
            blocks=(caption, diagram_label, formula, formula_number),
            caption_index=0,
            blocking_bboxes=((150.0, 548.0, 540.0, 564.0),),
            noise_bboxes=(),
        )

        self.assertIsNotNone(crop)
        self.assertGreater(crop[3], diagram_label.bbox[3])
        self.assertLessEqual(crop[3], formula.bbox[1] - 8.0)

    def test_figure_crop_stops_before_a_following_table_caption(self) -> None:
        """Table captions belong only to Table evidence, never to a Figure crop."""

        caption = DocumentBlock(
            1,
            (80.0, 120.0, 540.0, 142.0),
            DocumentBlockKind.TEXT,
            "Figure 29-6. Host output reference receiver",
            0,
            "test",
        )
        diagram_label = DocumentBlock(
            1,
            (210.0, 420.0, 400.0, 434.0),
            DocumentBlockKind.TEXT,
            "TP1a Reference Rx",
            1,
            "test",
        )
        table_caption = DocumentBlock(
            1,
            (74.0, 548.0, 550.0, 564.0),
            DocumentBlockKind.TEXT,
            "Table 29-7. Host output 5-tap Reference FFE Characteristics",
            2,
            "test",
        )

        crop = _figure_crop_bbox(
            page_bbox=(0.0, 0.0, 612.0, 792.0),
            blocks=(caption, diagram_label, table_caption),
            caption_index=0,
            blocking_bboxes=(),
            noise_bboxes=(),
        )

        self.assertIsNotNone(crop)
        self.assertGreater(crop[3], diagram_label.bbox[3])
        self.assertLessEqual(crop[3], table_caption.bbox[1] - 8.0)

    def test_formula_like_figure_labels_do_not_end_the_figure_without_number_anchor(self) -> None:
        """Dimension labels and chart ticks remain Figure content, not standalone equations."""

        caption = DocumentBlock(
            1, (80.0, 100.0, 540.0, 118.0), DocumentBlockKind.TEXT,
            "Figure 30-14. Channel reference model", 0, "test",
        )
        dimension = DocumentBlock(
            1, (230.0, 320.0, 550.0, 334.0), DocumentBlockKind.TEXT,
            "host PCB < 11.75dB < 2.45 dB + cap < 1.8 dB", 1, "test",
        )
        chart_tick = DocumentBlock(
            1, (46.0, 380.0, 126.0, 394.0), DocumentBlockKind.TEXT,
            "12 R 0", 2, "test",
        )
        final_diagram_label = DocumentBlock(
            1, (210.0, 500.0, 400.0, 514.0), DocumentBlockKind.TEXT,
            "Fiber Interconnect", 3, "test",
        )

        crop = _figure_crop_bbox(
            page_bbox=(0.0, 0.0, 612.0, 792.0),
            blocks=(caption, dimension, chart_tick, final_diagram_label),
            caption_index=0,
            blocking_bboxes=(),
            noise_bboxes=(),
            vector_graphic_bboxes=((90.0, 150.0, 520.0, 530.0),),
        )

        self.assertIsNotNone(crop)
        self.assertGreater(crop[3], final_diagram_label.bbox[3])

    def test_unnumbered_formula_below_vector_graphic_ends_the_figure(self) -> None:
        """An equation below a proven plot is separate even when it has no number."""

        caption = DocumentBlock(
            1, (80.0, 100.0, 540.0, 118.0), DocumentBlockKind.TEXT,
            "Figure 30-3. S-parameter limit", 0, "test",
        )
        axis_label = DocumentBlock(
            1, (210.0, 430.0, 400.0, 444.0), DocumentBlockKind.TEXT,
            "Frequency (GHz)", 1, "test",
        )
        formula = DocumentBlock(
            1, (140.0, 500.0, 480.0, 514.0), DocumentBlockKind.TEXT,
            "SCD11 ≤ -23+22*(f/fb) dB for 0.05 GHz ≤ f ≤ fb/2", 2, "test",
        )

        crop = _figure_crop_bbox(
            page_bbox=(0.0, 0.0, 612.0, 792.0),
            blocks=(caption, axis_label, formula),
            caption_index=0,
            blocking_bboxes=(),
            noise_bboxes=(),
            vector_graphic_bboxes=((130.0, 180.0, 500.0, 470.0),),
        )

        self.assertIsNotNone(crop)
        self.assertGreater(crop[3], axis_label.bbox[3])
        self.assertLessEqual(crop[3], formula.bbox[1] - 8.0)

    def test_isolated_right_edge_noise_does_not_clip_source_content(self) -> None:
        """One footer fragment near an edge cannot redefine the body boundary."""

        bounds = _content_horizontal_bounds(
            (0.0, 0.0, 612.0, 792.0),
            ((508.0, 746.0, 540.0, 756.0),),
        )

        self.assertEqual((21.42, 590.58), bounds)

    def test_tall_repeated_right_gutter_can_trim_source_content(self) -> None:
        """Only a dense edge column spanning the page may establish a gutter."""

        gutter = tuple(
            (555.0, float(top), 568.0, float(top + 10))
            for top in range(100, 701, 40)
        )

        left, right = _content_horizontal_bounds(
            (0.0, 0.0, 612.0, 792.0),
            gutter,
        )

        self.assertEqual(21.42, left)
        self.assertEqual(555.0, right)

    def test_coordinate_figure_caption_owns_page_even_when_section_body_omits_it(self) -> None:
        """Full-page block evidence prevents a sibling prose card from borrowing a Figure."""

        blocks = (
            DocumentBlock(
                1,
                (50.0, 80.0, 560.0, 95.0),
                DocumentBlockKind.TEXT,
                "Figure 30-13. Reference CTLE transfer function",
                0,
                "test",
            ),
            DocumentBlock(
                1,
                (72.0, 420.0, 500.0, 432.0),
                DocumentBlockKind.TEXT,
                "The input voltage tolerance tests the accepted amplitude.",
                1,
                "test",
            ),
        )

        self.assertTrue(_page_contains_figure_caption(blocks))

    def test_source_crop_preserves_original_pixels_without_overlay(self) -> None:
        """Source provenance must stay raw; only structured text owns change colors."""

        source = Image.new("RGB", (120, 80), "white")
        draw = ImageDraw.Draw(source)
        draw.rectangle((35, 35, 85, 45), fill="black")

        crop_bbox = (20.0, 20.0, 100.0, 60.0)
        cropped = _source_crop(
            source,
            page_bbox=(0.0, 0.0, 120.0, 80.0),
            crop_bbox=crop_bbox,
        )

        self.assertEqual((80, 40), cropped.size)
        self.assertEqual(source.crop((20, 20, 100, 60)).tobytes(), cropped.tobytes())

    def test_prose_crop_expands_to_the_complete_connected_paragraph(self) -> None:
        """A changed line must not leave the final continuation line half visible."""

        blocks = (
            DocumentBlock(
                1,
                (80.0, 80.0, 530.0, 92.0),
                DocumentBlockKind.TEXT,
                "Ceeq is derived from the FFE tap weights and is measured",
                0,
                "test",
            ),
            DocumentBlock(
                1,
                (80.0, 93.0, 520.0, 105.0),
                DocumentBlockKind.TEXT,
                "after the CTLE in the reference receiver.",
                1,
                "test",
            ),
            DocumentBlock(
                1,
                (80.0, 106.0, 250.0, 118.0),
                DocumentBlockKind.TEXT,
                "measurement methods. 4",
                2,
                "test",
            ),
            DocumentBlock(
                1,
                (80.0, 140.0, 300.0, 154.0),
                DocumentBlockKind.TEXT,
                "29.3.14 Overshoot/Undershoot",
                3,
                "test",
            ),
        )
        selected = (blocks[0].bbox, blocks[1].bbox)
        expanded = _expand_boxes_to_complete_paragraph_lines(
            blocks,
            selected,
            allowed_text=(
                "Ceeq is derived from the FFE tap weights and is measured "
                "after the CTLE in the reference receiver. measurement methods."
            ),
        )

        self.assertEqual((blocks[0].bbox, blocks[1].bbox, blocks[2].bbox), expanded)

    def test_tiny_residual_after_margin_subtraction_is_dropped(self) -> None:
        """A one-letter sliver must never be enlarged into a full report panel."""

        regions = _subtract_excluded_regions(
            (10.0, 10.0, 100.0, 30.0),
            ((0.0, 0.0, 97.0, 80.0),),
        )

        self.assertEqual((), regions)

    def test_source_crop_regions_split_around_a_recognized_table(self) -> None:
        """Text above and below a Table must become two crops that never include it."""

        table = (20.0, 100.0, 580.0, 300.0)
        crops = _crop_regions(
            page_bbox=(0.0, 0.0, 612.0, 792.0),
            boxes=((40.0, 60.0, 560.0, 82.0), (40.0, 330.0, 560.0, 352.0)),
            blocking_bboxes=(table,),
            noise_bboxes=(),
        )

        self.assertEqual(2, len(crops))
        self.assertLessEqual(crops[0][3], table[1])
        self.assertGreaterEqual(crops[1][1], table[3])
        self.assertTrue(all((crop[2] - crop[0]) / (crop[3] - crop[1]) > 1.2 for crop in crops))

    def test_block_matching_is_restricted_to_the_current_section_page_body(self) -> None:
        """A parent card cannot borrow a child heading from the same physical page."""

        blocks = (
            DocumentBlock(1, (40.0, 40.0, 560.0, 60.0), DocumentBlockKind.TEXT, "30.3 Electrical Characteristics", 0, "test"),
            DocumentBlock(1, (40.0, 70.0, 560.0, 95.0), DocumentBlockKind.TEXT, "Hosts shall meet the applicable specifications defined in Table 30-1.", 1, "test"),
            DocumentBlock(1, (40.0, 150.0, 560.0, 170.0), DocumentBlockKind.TEXT, "30.3.1 End-to-end linear channel description", 2, "test"),
        )

        regions, matched = _highlight_boxes(
            blocks,
            ("Hosts shall meet the applicable specifications defined in Table 30-1.",),
            allowed_text="30.3 Electrical Characteristics\nHosts shall meet the applicable specifications defined in Table 30-1.",
        )

        self.assertEqual(1, matched)
        self.assertEqual(((40.0, 70.0, 560.0, 95.0),), regions)

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

    def test_partial_table_overlap_is_subtracted_from_a_prose_highlight(self) -> None:
        """A text block crossing a table boundary must not tint the table-owned pixels."""

        block = DocumentBlock(
            page_number=1,
            bbox=(10.0, 10.0, 100.0, 40.0),
            kind=DocumentBlockKind.TEXT,
            text="Receiver voltage limit changes from 100 mV to 120 mV.",
            reading_order=0,
            source_engine="test",
        )
        table_bbox = (0.0, 30.0, 120.0, 60.0)

        regions, matched_snippets = _highlight_boxes(
            (block,),
            ("Receiver voltage limit changes from 100 mV to 120 mV.",),
            excluded_bboxes=(table_bbox,),
        )

        self.assertEqual(1, matched_snippets)
        self.assertEqual(((10.0, 10.0, 100.0, 30.0),), regions)

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
