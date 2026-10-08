"""跨层合同回归：通过真实公开入口验证分析中发现的反例。"""
import json
import tempfile
import unittest
from dataclasses import replace
from decimal import localcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz
from PIL import Image, ImageDraw

from protocol_pdf_diff import accuracy_evaluation as gold
from protocol_pdf_diff.compare import compare_extractions, _match_sections
from protocol_pdf_diff.comparison_content import build_comparison_view, _canonical_inline_number
from protocol_pdf_diff.models import DiffOptions, ExtractionResult, PageText, Section, TableVisual
from protocol_pdf_diff.pdf_extract import _build_table_visual, _table_screenshot_image, extract_pdf_text
from protocol_pdf_diff.reporting import write_reports
from protocol_pdf_diff.sectioning import section_document
from protocol_pdf_diff.table_view_transaction import run_comparison
from protocol_pdf_diff.text_utils import canonical_content_number
from protocol_pdf_diff.visual_watchdog import review_source_pixels


def section(n, title, body):
    heading = f"{n} {title}"
    return Section(str(n), heading, title, 1, (heading,), (str(n),), 1, 1, body)


def multipage_pdf(path, values):
    with fitz.open() as document:
        for index, value in enumerate(values):
            page = document.new_page(width=612, height=792)
            text = ("1 Receiver requirements\n" if index == 0 else "")
            text += f"The receiver calibration voltage for procedure {index} shall be {value} V in the selected mode.\n"
            text += "\n".join(f"Reference setting {j} remains unchanged throughout the complete measurement sequence." for j in range(8))
            assert page.insert_textbox((60, 60, 550, 650), text, fontsize=11) >= 0
            page.draw_rect((450, 680, 480, 710), color=(0, 0, 1))
        document.save(path)


class CoreArchitectureTests(unittest.TestCase):
    def test_display_budget_does_not_change_production_content_or_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            multipage_pdf(root / 'old.pdf', (3, 20))
            multipage_pdf(root / 'new.pdf', (5, 30))
            observations = []
            for limit in (1, 20):
                outcome = run_comparison(root / 'old.pdf', root / 'new.pdf', root / str(limit),
                                         DiffOptions(max_snippets_per_section=limit))
                payload = json.loads(outcome.outputs['json'].read_text())
                self.assertEqual(144, payload['provenance']['effective_thresholds']['visual_render_dpi'])
                audit = outcome.selected_result.provenance.visual_watchdog_audit
                observations.append((audit.semantic_change_pages, payload['coverage_review_items'], payload['content_changes']))
                self.assertIsNotNone(outcome.final_view)
                self.assertIn('30 V', outcome.outputs['markdown'].read_text())
            self.assertEqual(observations[0], observations[1])
            self.assertEqual({(1, None), (2, None), (None, 1), (None, 2)}, set(observations[0][0]))

    def test_inserted_same_number_cannot_steal_unique_full_body(self):
        body = 'The receiver shall measure the signal using the specified calibration fixture before the measurement starts.'
        old = [section(1, 'Scope', 'This document defines electrical requirements.'), section(2, 'Timing', body),
               section(3, 'Limits', 'The final limit shall be recorded in the approved test report.')]
        new = [old[0], section(2, 'Timing', body.replace('specified', 'auxiliary')),
               section(3, 'Timing', body), section(4, 'Limits', old[2].body)]
        for a, b, expected in ((old, new, {(0, 0), (1, 2), (2, 3)}),
                               (new, old, {(0, 0), (2, 1), (3, 2)})):
            pairs = {(i, j) for i, j, *_ in _match_sections(a, b, DiffOptions()) if i is not None and j is not None}
            self.assertEqual(expected, pairs)

    def test_plain_decimal_precision_is_consistent_in_prose_and_table(self):
        options = DiffOptions(comparison_profile='general', visual_watchdog=False)
        old, new = 'Current limit is 1.0 V.', 'Current limit is 1 V.'
        result = compare_extractions(ExtractionResult(Path('old.pdf'), [PageText(1, '1 Limits\n' + old)]),
                                     ExtractionResult(Path('new.pdf'), [PageText(1, '1 Limits\n' + new)]), options)
        prose = build_comparison_view(result, options)
        def table(text):
            return TableVisual(1, 1, 'Table 1. Limits', (10, 100, 300, 200), '',
                               ['表格行: T1 | Parameter=Capture | Details=' + text], '')
        tables = build_comparison_view(replace(result, changes=[], old_table_visuals=[table(old)],
                                              new_table_visuals=[table(new)]), options)
        self.assertEqual(1, len(prose.reader_changes))
        self.assertEqual(1, len(tables.reader_table_changes))
        with localcontext() as context:
            context.prec = 6
            for value in ('1234567890123456789012345678901', '1234567890123456789012345678902'):
                self.assertEqual(value, canonical_content_number(value))
                self.assertEqual(value, _canonical_inline_number(value))

    def test_report_cannot_reinterpret_completed_profile(self):
        options = DiffOptions(comparison_profile='protocol')
        result = compare_extractions(ExtractionResult(Path('old.pdf'), [PageText(1, '1 Scope\nOld body.')]),
                                     ExtractionResult(Path('new.pdf'), [PageText(1, '1 Scope\nNew body.')]), options)
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, 'comparison_profile'):
            write_reports(result, directory, replace(options, comparison_profile='general'))

    def test_render_failure_retains_structure_and_physical_boundary(self):
        page = SimpleNamespace(bbox=(0, 0, 612, 792), width=612, height=792)
        table = SimpleNamespace(bbox=(10, 100, 300, 200))
        with patch('protocol_pdf_diff.pdf_extract._table_screenshot_image', return_value=(None, (7, 97, 303, 203), 'render failure')):
            visual, warning = _build_table_visual(page, table, ['observed row'], 1, 1,
                                                  title='Table 1', content_fully_represented=True)
        self.assertEqual(table.bbox, visual.bbox)
        self.assertEqual((7, 97, 303, 203), visual.crop_bbox)
        self.assertEqual(['observed row'], visual.row_texts)
        self.assertTrue(visual.content_fully_represented)
        self.assertEqual('', visual.image_data_uri)
        self.assertEqual('render failure', warning)

    def test_recognition_crop_has_no_drawn_border(self):
        raw = Image.new('RGB', (400, 200), 'white')
        page = SimpleNamespace(bbox=(0, 0, 612, 792), width=612, height=792,
            crop=lambda box: SimpleNamespace(to_image=lambda **kw: SimpleNamespace(original=raw)))
        observed, _box, status = _table_screenshot_image(page, (10, 100, 300, 200))
        self.assertEqual('', status)
        self.assertEqual(((255, 255),) * 3, observed.getextrema())

    def test_small_residual_is_preserved_alongside_large_change(self):
        old = Image.new('RGB', (400, 400), 'white')
        draw = ImageDraw.Draw(old)
        draw.rectangle((30, 30, 31, 31), fill='black')
        draw.rectangle((240, 300, 260, 330), fill='black')
        new = Image.new('RGB', old.size, 'white')
        scope = dict(old_page_number=1, new_page_number=1, alignment_method='test',
                     old_page_bbox=(0, 0, 400, 400), new_page_bbox=(0, 0, 400, 400))
        review = review_source_pixels(old, new, **scope)
        self.assertEqual('changed', review.status)
        self.assertEqual((30, 30, 261, 331), review.item.diff_bbox)
        self.assertEqual(2, len(review.item.focus_regions))
        draw.rectangle((240, 300, 260, 330), fill='white')
        self.assertEqual('unresolved', review_source_pixels(old, new, **scope).status)

    def test_table_source_identity_and_section_survive_render_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'table.pdf'
            with fitz.open() as doc:
                page = doc.new_page(width=612, height=792)
                page.insert_text((60, 60), '1 Scope', fontsize=16)
                page.insert_text((60, 90), 'The following table states the original voltage requirements.')
                page.insert_text((60, 130), 'Table 1. Receiver settings')
                for y in (150, 180, 210): page.draw_line((60, y), (450, y))
                for x in (60, 250, 350, 450): page.draw_line((x, 150), (x, 210))
                for x, text in ((70, 'Parameter'), (260, 'Value'), (360, 'Unit')): page.insert_text((x, 170), text)
                for x, text in ((70, 'Voltage'), (260, '3.3'), (360, 'V')): page.insert_text((x, 200), text)
                page.insert_text((60, 270), '2 Timing', fontsize=16)
                page.insert_text((60, 300), 'The unrelated timing chapter starts after this table.')
                doc.save(path)
            baseline = extract_pdf_text(path)
            with patch('protocol_pdf_diff.pdf_extract._table_screenshot_image',
                       side_effect=lambda _p, b: (None, b, 'test render failure')):
                failed = extract_pdf_text(path)
            self.assertEqual(1, len(baseline.table_visuals))
            a, b = baseline.table_visuals[0], failed.table_visuals[0]
            self.assertEqual((a.source_id, a.bbox, a.row_texts), (b.source_id, b.bbox, b.row_texts))
            sections = section_document(failed)
            owners = [s for s in sections if b.source_id in s.table_source_ids]
            self.assertEqual(['Scope'], [s.title for s in owners])
            self.assertFalse(any('表格行:' in s.body for s in sections))

    def test_independent_oracle_catches_leak_on_each_real_surface(self):
        options = DiffOptions(visual_watchdog=False)
        result = compare_extractions(ExtractionResult(Path('old.pdf'), [PageText(1, '1 Limits\nThe limit shall be 3 V.')]),
                                     ExtractionResult(Path('new.pdf'), [PageText(1, '1 Limits\nThe limit shall be 5 V.')]), options)
        with tempfile.TemporaryDirectory() as directory:
            paths = write_reports(result, directory, options)
            readers = {'html': gold._read_visible_html_evidence, 'markdown': gold._read_markdown_evidence,
                       'text': gold._read_text_evidence}
            empty = Path(directory) / 'empty.txt'
            empty.write_text('')
            payload = json.loads(paths['json'].read_text())
            for change in payload['changes']: change['reader_card_id'] = None
            expected = dict(kind='text', old='3 V', new='5 V', reader_visible=False, occurrences=1)
            for leak in readers:
                surfaces = {key: reader(paths[key] if key == leak else empty) for key, reader in readers.items()}
                actual = gold._actual_events(payload, surfaces)
                _a, _b, failures = gold._match_expected_events([expected], actual, reader_surfaces=surfaces)
                self.assertTrue(any(leak + ': reader visibility mismatch' in f for f in failures), failures)
