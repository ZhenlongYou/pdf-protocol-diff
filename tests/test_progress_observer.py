"""Protect read-only progress reporting from changing comparison semantics."""

from __future__ import annotations

import unittest
import tempfile
import queue
from datetime import datetime
from pathlib import Path
from unittest import mock

import protocol_pdf_diff.compare as compare_module
import protocol_pdf_diff.pdf_extract as pdf_extract_module
from protocol_pdf_diff.models import DiffOptions, DiffResult, ExtractionResult
from protocol_pdf_diff.progress import ProgressEvent, notify_progress
from protocol_pdf_diff.models import VisualWatchdogAudit
from protocol_pdf_diff.desktop_gui import DesktopRunConfig, ProtocolDiffDesktopApp
from protocol_pdf_diff.pdf_extract import extract_pdf_text
from protocol_pdf_diff.sample_data import write_demo_pdfs, write_multipage_text_pdf
from protocol_pdf_diff.reporting import write_reports


class ProgressObserverTests(unittest.TestCase):
    """Progress callbacks are observational and must fail open."""

    def test_notify_progress_ignores_callback_failure(self) -> None:
        event = ProgressEvent(stage="read_old", side="old", completed_pages=1, total_pages=3)

        notify_progress(lambda _event: (_ for _ in ()).throw(RuntimeError("boom")), event)

    def test_extraction_reports_actual_selected_page_counts(self) -> None:
        events: list[ProgressEvent] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = write_multipage_text_pdf(
                Path(temp_dir) / "three-pages.pdf",
                [["1 First"], ["2 Second"], ["3 Third"]],
            )
            extraction = extract_pdf_text(
                pdf_path,
                start_page=2,
                end_page=3,
                progress_observer=events.append,
                progress_stage="read_new",
                progress_side="new",
            )

        self.assertEqual([2, 3], [page.page_number for page in extraction.pages])
        extracted = [event for event in events if event.stage == "read_new"]
        scanned = [event for event in events if event.stage == "read_new_scan"]
        self.assertEqual([0, 1, 2], [event.completed_pages for event in extracted])
        self.assertEqual([1, 2], [event.completed_pages for event in scanned])
        self.assertTrue(all(event.total_pages == 2 for event in events))
        self.assertTrue(all(event.side == "new" for event in events))

    def test_prescan_progress_precedes_normal_extraction_zero(self) -> None:
        timeline: list[str] = []
        original_coordinate_reader = pdf_extract_module.extract_pdfplumber_coordinate_words

        def record_coordinate_scan(*args: object, **kwargs: object) -> object:
            timeline.append("coordinate-scan")
            return original_coordinate_reader(*args, **kwargs)

        def record_progress(event: ProgressEvent) -> None:
            timeline.append(f"{event.stage}-{event.completed_pages}")

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = write_multipage_text_pdf(
                Path(temp_dir) / "two-pages.pdf",
                [["1 First"], ["2 Second"]],
            )
            with mock.patch(
                "protocol_pdf_diff.pdf_extract.extract_pdfplumber_coordinate_words",
                side_effect=record_coordinate_scan,
            ):
                extract_pdf_text(pdf_path, progress_observer=record_progress)

        last_scan = max(index for index, item in enumerate(timeline) if item == "coordinate-scan")
        first_scan_progress = timeline.index("read_pdf_scan-1")
        first_extraction = timeline.index("read_pdf-0")
        self.assertLess(first_scan_progress, last_scan)
        self.assertGreater(first_extraction, last_scan)

    def test_run_diff_emits_real_stage_order_without_changing_result(self) -> None:
        old_extraction = ExtractionResult(Path("old.pdf"), [], total_pages=3)
        new_extraction = ExtractionResult(Path("new.pdf"), [], total_pages=4)
        expected = DiffResult(Path("old.pdf"), Path("new.pdf"), [], [], [], [])
        audit = VisualWatchdogAudit(
            enabled=True,
            attempted=True,
            backend_available=True,
            eligible_page_pair_count=0,
            checked_page_pair_count=0,
            failed_page_pair_count=0,
            ambiguous_page_count=0,
            excluded_region_count=0,
            complete=True,
            source_hashes_match=False,
        )
        events: list[ProgressEvent] = []

        with (
            mock.patch.object(
                compare_module,
                "extract_pdf_text",
                side_effect=[old_extraction, new_extraction, old_extraction, new_extraction],
            ),
            mock.patch.object(compare_module, "compare_extractions", return_value=expected),
            mock.patch.object(
                compare_module,
                "detect_visual_review_items",
                return_value=([], [], audit),
            ),
            mock.patch.object(
                compare_module,
                "build_prose_source_visuals",
                return_value=([], []),
            ),
        ):
            baseline = compare_module.run_diff("old.pdf", "new.pdf", DiffOptions())
            observed = compare_module.run_diff(
                "old.pdf",
                "new.pdf",
                DiffOptions(),
                progress_observer=events.append,
            )

        self.assertEqual(baseline, observed)
        stage_order = list(dict.fromkeys(event.stage for event in events))
        self.assertEqual(
            ["read_old", "read_new", "match_diff", "visual_evidence"],
            stage_order,
        )

    def test_run_diff_ignores_observer_exceptions(self) -> None:
        extraction = ExtractionResult(Path("one.pdf"), [])
        expected = DiffResult(Path("old.pdf"), Path("new.pdf"), [], [], [], [])
        audit = VisualWatchdogAudit(
            enabled=True,
            attempted=True,
            backend_available=True,
            eligible_page_pair_count=0,
            checked_page_pair_count=0,
            failed_page_pair_count=0,
            ambiguous_page_count=0,
            excluded_region_count=0,
            complete=True,
            source_hashes_match=False,
        )

        with (
            mock.patch.object(compare_module, "extract_pdf_text", return_value=extraction),
            mock.patch.object(compare_module, "compare_extractions", return_value=expected),
            mock.patch.object(
                compare_module,
                "detect_visual_review_items",
                return_value=([], [], audit),
            ),
            mock.patch.object(
                compare_module,
                "build_prose_source_visuals",
                return_value=([], []),
            ),
        ):
            observed = compare_module.run_diff(
                "old.pdf",
                "new.pdf",
                DiffOptions(),
                progress_observer=lambda _event: (_ for _ in ()).throw(RuntimeError("boom")),
            )

        self.assertEqual(expected, observed)

    def test_desktop_worker_adds_report_stage_before_success(self) -> None:
        app = object.__new__(ProtocolDiffDesktopApp)
        app._result_queue = queue.Queue()
        config = DesktopRunConfig(
            old_pdf=Path("old.pdf"),
            new_pdf=Path("new.pdf"),
            output_dir=Path("reports"),
            options=DiffOptions(),
        )
        result = DiffResult(Path("old.pdf"), Path("new.pdf"), [], [], [], [])

        def fake_run_diff(*_args: object, **kwargs: object) -> DiffResult:
            kwargs["progress_observer"](ProgressEvent(stage="visual_evidence"))
            return result

        with (
            mock.patch("protocol_pdf_diff.desktop_gui.run_diff", side_effect=fake_run_diff),
            mock.patch(
                "protocol_pdf_diff.desktop_gui.write_reports",
                return_value={"html": Path("report.html")},
            ),
        ):
            app._run_worker(config)

        queued = []
        while not app._result_queue.empty():
            queued.append(app._result_queue.get_nowait())
        self.assertEqual(
            ["visual_evidence", "report"],
            [payload.stage for status, payload in queued if status == "progress"],
        )
        self.assertEqual("success", queued[-1][0])

    def test_real_demo_reports_are_byte_identical_with_or_without_observer(self) -> None:
        events: list[ProgressEvent] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_pdf, new_pdf = write_demo_pdfs(root / "inputs")
            options = DiffOptions()
            baseline = compare_module.run_diff(old_pdf, new_pdf, options)
            observed = compare_module.run_diff(
                old_pdf,
                new_pdf,
                options,
                progress_observer=events.append,
            )
            fixed_now = datetime(2026, 8, 28, 12, 34, 56)
            with mock.patch("protocol_pdf_diff.reporting.datetime") as report_datetime:
                report_datetime.now.return_value = fixed_now
                baseline_outputs = write_reports(baseline, root / "baseline", options)
                observed_outputs = write_reports(observed, root / "observed", options)

            self.assertEqual(baseline, observed)
            for key in ("html", "text", "markdown", "csv", "table_csv", "json"):
                self.assertEqual(
                    baseline_outputs[key].read_bytes(),
                    observed_outputs[key].read_bytes(),
                    key,
                )
        self.assertEqual(
            ["read_old", "read_old_scan", "read_new", "read_new_scan", "match_diff", "sectioning", "match_exact", "match_fallback", "visual_evidence"],
            list(dict.fromkeys(event.stage for event in events)),
        )


if __name__ == "__main__":
    unittest.main()
